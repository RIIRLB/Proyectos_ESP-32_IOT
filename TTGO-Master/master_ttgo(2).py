# ============================================================
#  MASTER_TTGO — Protocolo PIF (Propagación con Retroalimentación)
#  Wake: lightsleep por timer (sin GPIO33)
#  Botones: GPIO0  (btn_dir) → ver nodos registrados
#           GPIO35 (btn_env) → medir DHT11 propio + REQ:ALL manual
#  Sensor propio: DHT11 via sens.py (sens_v3)
# ============================================================
#
#  Archivos necesarios en la TTGO:
#    - sens_v3.py   → renombrar a sens.py
#    - tft_config.py
#    - st7789py.py
#    - comfortaa_24.py
#    - comfortaa_16.py
#
# ============================================================

import gc
import network
import espnow
import machine
from machine import Pin, lightsleep
import utime
import json

import tft_config
import st7789py as st7789
import comfortaa_16 as font_sm
import comfortaa_24 as font_md
from sens import Sensores
from umqtt.simple import MQTTClient

gc.collect()

# ───────────────────────────────────────────────
#  CONFIGURACIÓN
# ───────────────────────────────────────────────
WIFI_SSID      = "Arte_Tenda5"
WIFI_PASS      = "Lab4rt3#"
MQTT_BROKER    = "192.168.1.146"
CLIENT_ID      = "MASTER_TTGO_GATEWAY"

SLEEP_MS       = 10_000
VENTANA_MS     = 5_000
BROADCAST_REPS = 4
BROADCAST_DLY  = 500

TOPIC_SUB      = b"comandos/mesh"
TOPIC_PUB      = b"datos/sensores"
BROADCAST_MAC  = b'\xff\xff\xff\xff\xff\xff'

# ───────────────────────────────────────────────
#  HARDWARE — igual que ttgo_master_v4
# ───────────────────────────────────────────────
tft = tft_config.config(rotation=1)

hw = Sensores(
    tft       = tft,
    p_dht     = 15,
    p_mq135   = 34,
    p_btn_dir = 0,    # GPIO0  — ver nodos
    p_btn_env = 35    # GPIO35 — medir y enviar
)

backlight = Pin(4, Pin.OUT)
backlight.value(1)

# ───────────────────────────────────────────────
#  ESTADO GLOBAL
# ───────────────────────────────────────────────
client            = None
en_now            = None
comando_pendiente = None
nodos_vistos      = []

# ───────────────────────────────────────────────
#  DISPLAY — helpers de estado
#  (hw.mostrar_en_pantalla se usa para datos de sensores)
# ───────────────────────────────────────────────
W = tft.physical_height
H = tft.physical_width

def cx(font, texto):
    return max(0, (W - tft.write_width(font, texto)) // 2)

def pantalla_estado(titulo, linea1="", linea2="", color_titulo=st7789.CYAN):
    tft.fill(st7789.BLACK)
    tft.write(font_md, titulo, cx(font_md, titulo), 5,  color_titulo)
    if linea1:
        tft.write(font_sm, linea1, cx(font_sm, linea1), 50, st7789.WHITE)
    if linea2:
        tft.write(font_sm, linea2, cx(font_sm, linea2), 85, st7789.YELLOW)

def pantalla_bienvenida():
    tft.fill(st7789.BLACK)
    tft.write(font_md, "PIF MESH",    cx(font_md, "PIF MESH"),    5,  st7789.GREEN)
    tft.write(font_sm, "LAB-ARTE",    cx(font_sm, "LAB-ARTE"),    45, st7789.CYAN)
    tft.write(font_sm, "MASTER NODE", cx(font_sm, "MASTER NODE"), 70, st7789.WHITE)
    tft.write(font_sm, CLIENT_ID,     cx(font_sm, CLIENT_ID),     95, st7789.YELLOW)

def pantalla_sleep():
    tft.fill(st7789.BLACK)
    tft.write(font_sm, "sleeping...", cx(font_sm, "sleeping..."), 55, st7789.CYAN)
    backlight.value(0)   # Apagar backlight para ahorrar energía

def pantalla_nodos():
    tft.fill(st7789.BLACK)
    tft.write(font_sm, "NODOS ACTIVOS", cx(font_sm, "NODOS ACTIVOS"), 0, st7789.CYAN)
    y = 28
    if not nodos_vistos:
        tft.write(font_sm, "Sin nodos aun", cx(font_sm, "Sin nodos aun"), y, st7789.RED)
    for nid in nodos_vistos[-3:]:
        tft.write(font_sm, nid, cx(font_sm, nid), y, st7789.GREEN)
        y += 30

def pantalla_feedback_nodo(nodo, tipo, valor):
    tft.fill(st7789.BLACK)
    tft.write(font_sm, nodo,       cx(font_sm, nodo),       5,  st7789.CYAN)
    tft.write(font_md, tipo,       cx(font_md, tipo),       35, st7789.WHITE)
    tft.write(font_md, str(valor), cx(font_md, str(valor)), 72, st7789.GREEN)

# ───────────────────────────────────────────────
#  WIFI — solo reconecta si hace falta
# ───────────────────────────────────────────────
def verificar_wifi():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if sta.isconnected():
        return True
    pantalla_estado("WiFi", "Conectando...", WIFI_SSID)
    sta.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(100):
        if sta.isconnected():
            pantalla_estado("WiFi OK", sta.ifconfig()[0], color_titulo=st7789.GREEN)
            utime.sleep_ms(600)
            return True
        utime.sleep_ms(100)
    pantalla_estado("WiFi ERROR", "Sin conexion", color_titulo=st7789.RED)
    utime.sleep_ms(800)
    return False

# ───────────────────────────────────────────────
#  MQTT — persistente, solo reconecta si se cayó
# ───────────────────────────────────────────────
def mqtt_callback(topic, msg):
    global comando_pendiente
    orden = msg.decode().strip()
    print("[MQTT] Orden:", orden)
    comando_pendiente = orden

def verificar_mqtt():
    global client
    # Si ya hay cliente, hacer ping para verificar que sigue vivo
    if client is not None:
        try:
            client.ping()
            client.check_msg()   # Recibir mensajes pendientes
            return True
        except:
            client = None        # Se cayó, reconectar abajo

    # Primera conexión o reconexión
    pantalla_estado("MQTT", "Conectando...", MQTT_BROKER)
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=60)
        client.set_callback(mqtt_callback)
        client.connect()
        client.subscribe(TOPIC_SUB)
        pantalla_estado("MQTT OK", MQTT_BROKER, color_titulo=st7789.GREEN)
        utime.sleep_ms(500)
        return True
    except Exception as e:
        pantalla_estado("MQTT ERROR", str(e)[:22], color_titulo=st7789.RED)
        utime.sleep_ms(800)
        client = None
        return False

def publicar_dato(node_id, tipo, valor):
    if client is None:
        return
    hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
    payload = "{},{},{},{}".format(hora, node_id, tipo, valor)
    try:
        client.publish(TOPIC_PUB, payload)
        print("[MQTT] ->", payload)
    except Exception as e:
        print("[MQTT] Error:", e)

# ───────────────────────────────────────────────
#  SENSOR PROPIO — DHT11 del Master via sens_v3
# ───────────────────────────────────────────────
def medir_maestro():
    """
    Muestra DHT11 en pantalla (igual que ttgo_master_v4) y publica al broker.
    """
    hw.mostrar_en_pantalla("DHT11", status="MIDIENDO...")
    t, h = hw.leer_dht()
    hw.mostrar_en_pantalla("DHT11", status="ENVIANDO...")
    publicar_dato(CLIENT_ID, "Temperatura", t)
    publicar_dato(CLIENT_ID, "Humedad", h)
    hw.mostrar_en_pantalla("DHT11", status="LISTO!")
    utime.sleep_ms(1000)

# ───────────────────────────────────────────────
#  ESP-NOW
# ───────────────────────────────────────────────
def init_espnow():
    global en_now
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    en_now = espnow.ESPNow()
    en_now.active(True)
    en_now.add_peer(BROADCAST_MAC)

def cerrar_espnow():
    global en_now
    if en_now:
        en_now.active(False)
        en_now = None

def enviar_onda_pif(cmd, destino_id=None):
    target = destino_id if destino_id else "ALL"
    msg = json.dumps({
        "type"  : "WAVE",
        "cmd"   : cmd,
        "target": target,
        "origin": "MASTER"
    })
    hw.mostrar_en_pantalla("DHT11",
        status="PIF->{}".format(target[:8]))
    for _ in range(BROADCAST_REPS):
        en_now.send(BROADCAST_MAC, msg)
        utime.sleep_ms(BROADCAST_DLY)

def escuchar_feedback():
    global nodos_vistos
    fin = utime.ticks_add(utime.ticks_ms(), VENTANA_MS)
    hw.mostrar_en_pantalla("DHT11", status="ESCUCHANDO...")
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        host, msg = en_now.recv(50)
        if msg:
            try:
                data = json.loads(msg.decode())
                if data.get("type") == "FEEDBACK":
                    nodo = data.get("id", "?")
                    if nodo not in nodos_vistos:
                        nodos_vistos.append(nodo)
                    for m in data.get("payload", []):
                        publicar_dato(nodo, m["tipo"], m["val"])
                        pantalla_feedback_nodo(nodo, m["tipo"], m["val"])
                        utime.sleep_ms(300)
                        recibidos += 1
            except Exception as e:
                print("[ESP-NOW] Error:", e)

    hw.mostrar_en_pantalla("DHT11",
        status="{} lect / {} nodos".format(recibidos, len(nodos_vistos)))
    utime.sleep_ms(1200)

# ───────────────────────────────────────────────
#  PROCESAR COMANDO MQTT
# ───────────────────────────────────────────────
def procesar_comando(orden):
    print("[CMD]", orden)
    init_espnow()

    if orden in ("REQ:ALL", "Iniciando OIF"):
        medir_maestro()
        enviar_onda_pif("REQ:ALL")
        escuchar_feedback()

    elif orden.startswith("REQ:"):
        destino = orden.split(":")[1]
        medir_maestro()
        enviar_onda_pif("REQ:SENSOR", destino_id=destino)
        escuchar_feedback()

    elif orden == "Emparejando":
        hw.mostrar_en_pantalla("DHT11", status="EMPAREJANDO...")
        enviar_onda_pif("PAIR")
        escuchar_feedback()

    else:
        hw.mostrar_en_pantalla("DHT11", status="CMD:?")
        utime.sleep_ms(600)

    cerrar_espnow()

# ───────────────────────────────────────────────
#  BOTONES
#  GPIO35 (btn_env) → medir DHT11 propio + REQ:ALL
#  GPIO0  (btn_dir) → mostrar nodos registrados
# ───────────────────────────────────────────────
def revisar_botones():
    # btn_env (GPIO35) — Medir y enviar REQ:ALL manual
    if hw.btn_env.value() == 0:
        utime.sleep_ms(50)           # debounce
        if hw.btn_env.value() == 0:
            if verificar_wifi() and verificar_mqtt():
                init_espnow()
                medir_maestro()
                enviar_onda_pif("REQ:ALL")
                escuchar_feedback()
                cerrar_espnow()

    # btn_dir (GPIO0) — Ver lista de nodos
    if hw.btn_dir.value() == 0:
        utime.sleep_ms(50)
        if hw.btn_dir.value() == 0:
            pantalla_nodos()
            utime.sleep_ms(2500)

# ───────────────────────────────────────────────
#  CICLO PRINCIPAL
# ───────────────────────────────────────────────
def ciclo():
    global comando_pendiente

    backlight.value(1)   # Encender pantalla al despertar

    # Revisar botones antes de cualquier otra cosa
    revisar_botones()

    # Conectar WiFi y MQTT (reutiliza si ya están activos)
    if not verificar_wifi():
        pantalla_sleep()
        lightsleep(SLEEP_MS)
        return

    if not verificar_mqtt():
        pantalla_sleep()
        lightsleep(SLEEP_MS)
        return

    # Ejecutar comando si llegó desde el servidor
    if comando_pendiente:
        orden = comando_pendiente
        comando_pendiente = None
        procesar_comando(orden)
    else:
        # Sin comando — mostrar sensor propio mientras espera
        t, h = hw.leer_dht()
        hw.mostrar_en_pantalla("DHT11", status="EN ESPERA")

    # Dormir
    pantalla_sleep()
    print("[Sleep] {}s...".format(SLEEP_MS // 1000))
    lightsleep(SLEEP_MS)
    print("[Wake]")

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
pantalla_bienvenida()
utime.sleep_ms(2000)

while True:
    ciclo()
