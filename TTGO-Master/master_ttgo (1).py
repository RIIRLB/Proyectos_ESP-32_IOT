# ============================================================
#  MASTER_TTGO — Protocolo PIF (Propagación con Retroalimentación)
#  Modo: lightsleep 10s → ventana MQTT → broadcast/unicast ESP-NOW
#  Display: ST7789 135x240 vía tft_config + fuentes comfortaa
#  Botones: GPIO0 (izq/acción) | GPIO35 (der)
#  Autor: generado para proyecto LAB-ARTE / PIF Mesh
# ============================================================
#
#  Archivos necesarios en la TTGO (misma carpeta):
#    - tft_config.py
#    - st7789py.py
#    - comfortaa_16.py
#    - comfortaa_24.py
#    - tft_buttons.py
#
# ============================================================

import gc
import network
import espnow
import machine
import utime
import json
import esp32

import tft_config
import st7789py as st7789
import comfortaa_16 as font_sm   # Texto pequeño (estados, IPs)
import comfortaa_24 as font_md   # Texto principal
from tft_buttons import Buttons
from umqtt.simple import MQTTClient

gc.collect()

# ───────────────────────────────────────────────
#  CONFIGURACIÓN
# ───────────────────────────────────────────────
WIFI_SSID      = "Totalplay-C5AC" #"La_Red_WiFi"
WIFI_PASS      = "C5AC642BDVePRn6Z" #"El_Password"
MQTT_BROKER    = "192.168.100.132"
CLIENT_ID      = "MASTER_TTGO_GATEWAY"

SLEEP_MS       = 10_000
VENTANA_MS     = 5_000
BROADCAST_REPS = 4
BROADCAST_DLY  = 500

TOPIC_SUB      = b"comandos/mesh"
TOPIC_PUB      = b"datos/sensores"
BROADCAST_MAC  = b'\xff\xff\xff\xff\xff\xff'

# ───────────────────────────────────────────────
#  HARDWARE
# ───────────────────────────────────────────────
tft = tft_config.config(tft_config.WIDE)   # Pantalla horizontal 240x135
btn = Buttons()                             # btn.left=GPIO0, btn.right=GPIO35

# Estado global
comando_pendiente = None
client            = None
en_now            = None
nodos_vistos      = []    # IDs de esclavas que han respondido

# ───────────────────────────────────────────────
#  DISPLAY — Helpers
# ───────────────────────────────────────────────
# En modo WIDE: physical_height=240 (eje X), physical_width=135 (eje Y)
# tft.write(font, texto, x, y, color)

W = tft.physical_height   # 240 — ancho real en WIDE
H = tft.physical_width    # 135 — alto real en WIDE

def cx(font, texto):
    """Calcula x para centrar texto horizontalmente."""
    return max(0, (W - tft.write_width(font, texto)) // 2)

def pantalla_estado(titulo, linea1="", linea2="", linea3="", color_titulo=st7789.CYAN):
    """
    Pantalla de estado estándar con título + hasta 3 líneas.
    Layout vertical (WIDE, 135px de alto):
      y=0  → título   (font_md, HEIGHT=31)
      y=38 → linea1   (font_sm, HEIGHT=~22)
      y=62 → linea2   (font_sm)
      y=86 → linea3   (font_sm) — en amarillo como nota
    """
    tft.fill(st7789.BLACK)
    tft.write(font_md, titulo, cx(font_md, titulo), 0, color_titulo)
    if linea1:
        tft.write(font_sm, linea1, cx(font_sm, linea1), 38, st7789.WHITE)
    if linea2:
        tft.write(font_sm, linea2, cx(font_sm, linea2), 62, st7789.WHITE)
    if linea3:
        tft.write(font_sm, linea3, cx(font_sm, linea3), 86, st7789.YELLOW)

def pantalla_bienvenida():
    tft.fill(st7789.BLACK)
    tft.write(font_md, "PIF MESH",    cx(font_md, "PIF MESH"),    4,  st7789.GREEN)
    tft.write(font_sm, "LAB-ARTE",    cx(font_sm, "LAB-ARTE"),    40, st7789.CYAN)
    tft.write(font_sm, "MASTER NODE", cx(font_sm, "MASTER NODE"), 65, st7789.WHITE)
    tft.write(font_sm, CLIENT_ID,     cx(font_sm, CLIENT_ID),     90, st7789.YELLOW)

def pantalla_sleep():
    tft.fill(st7789.BLACK)
    tft.write(font_sm, "sleeping...", cx(font_sm, "sleeping..."), 55, st7789.CYAN)

def pantalla_feedback(nodo, tipo, valor):
    """Muestra brevemente la última medición recibida de una esclava."""
    tft.fill(st7789.BLACK)
    tft.write(font_sm, nodo,       cx(font_sm, nodo),       8,  st7789.CYAN)
    tft.write(font_md, tipo,       cx(font_md, tipo),       34, st7789.WHITE)
    tft.write(font_md, str(valor), cx(font_md, str(valor)), 70, st7789.GREEN)

def pantalla_nodos():
    """Lista los nodos que han enviado feedback."""
    tft.fill(st7789.BLACK)
    tft.write(font_sm, "NODOS ACTIVOS", cx(font_sm, "NODOS ACTIVOS"), 0, st7789.CYAN)
    y = 26
    for nid in nodos_vistos[-4:]:   # Muestra hasta 4 nodos
        tft.write(font_sm, nid, cx(font_sm, nid), y, st7789.GREEN)
        y += 26

# ───────────────────────────────────────────────
#  WIFI
# ───────────────────────────────────────────────
def conectar_wifi():
    pantalla_estado("WiFi", "Conectando...", WIFI_SSID)
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if sta.isconnected():
        ip = sta.ifconfig()[0]
        pantalla_estado("WiFi OK", ip, color_titulo=st7789.GREEN)
        utime.sleep_ms(800)
        return True
    sta.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(100):
        if sta.isconnected():
            ip = sta.ifconfig()[0]
            pantalla_estado("WiFi OK", ip, color_titulo=st7789.GREEN)
            utime.sleep_ms(800)
            return True
        utime.sleep_ms(100)
    pantalla_estado("WiFi ERROR", "Sin conexion", color_titulo=st7789.RED)
    utime.sleep_ms(1000)
    return False

def desconectar_wifi():
    sta = network.WLAN(network.STA_IF)
    sta.disconnect()
    sta.active(False)

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

def enviar_onda_pif(cmd, destino_id=None):
    target = destino_id if destino_id else "ALL"
    msg = json.dumps({
        "type"  : "WAVE",
        "cmd"   : cmd,
        "target": target,
        "origin": "MASTER"
    })
    label = "REQ:" + (destino_id if destino_id else "ALL")
    pantalla_estado("ONDA PIF", label, "x{} repeticiones".format(BROADCAST_REPS), color_titulo=st7789.YELLOW)
    for _ in range(BROADCAST_REPS):
        en_now.send(BROADCAST_MAC, msg)
        utime.sleep_ms(BROADCAST_DLY)

# ───────────────────────────────────────────────
#  MQTT
# ───────────────────────────────────────────────
def mqtt_callback(topic, msg):
    global comando_pendiente
    orden = msg.decode().strip()
    print("[MQTT] Orden recibida:", orden)
    comando_pendiente = orden

def conectar_mqtt():
    global client
    pantalla_estado("MQTT", "Conectando...", MQTT_BROKER)
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(mqtt_callback)
        client.connect()
        client.subscribe(TOPIC_SUB)
        pantalla_estado("MQTT OK", MQTT_BROKER, color_titulo=st7789.GREEN)
        utime.sleep_ms(600)
        return True
    except Exception as e:
        pantalla_estado("MQTT ERROR", str(e)[:20], color_titulo=st7789.RED)
        utime.sleep_ms(1000)
        client = None
        return False

def publicar_dato(node_id, tipo, valor):
    if client is None:
        return
    hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
    payload = "{},{},{},{}".format(hora, node_id, tipo, valor)
    try:
        client.publish(TOPIC_PUB, payload)
        print("[MQTT] Publicado:", payload)
    except Exception as e:
        print("[MQTT] Error:", e)

def medir_maestro():
    tf = esp32.raw_temperature()
    tc = round((tf - 32) * 5 / 9, 2)
    publicar_dato("MASTER", "InternalTemp", tc)
    pantalla_feedback("MASTER", "Temp Int.", "{}C".format(tc))
    utime.sleep_ms(500)

# ───────────────────────────────────────────────
#  PROCESAR COMANDO
# ───────────────────────────────────────────────
def procesar_comando(orden):
    pantalla_estado("COMANDO", orden[:18], color_titulo=st7789.CYAN)
    utime.sleep_ms(400)

    if orden in ("REQ:ALL", "Iniciando OIF"):
        medir_maestro()
        enviar_onda_pif("REQ:ALL")

    elif orden.startswith("REQ:"):
        destino = orden.split(":")[1]
        medir_maestro()
        enviar_onda_pif("REQ:SENSOR", destino_id=destino)

    elif orden == "Emparejando":
        pantalla_estado("PAIR", "Buscando nodos...", color_titulo=st7789.CYAN)
        enviar_onda_pif("PAIR")

    else:
        pantalla_estado("?", "Desconocido", orden[:18], color_titulo=st7789.RED)
        utime.sleep_ms(800)

# ───────────────────────────────────────────────
#  ESCUCHAR FEEDBACK
# ───────────────────────────────────────────────
def escuchar_feedback():
    global nodos_vistos
    fin = utime.ticks_add(utime.ticks_ms(), VENTANA_MS)
    pantalla_estado("ESCUCHA", "Esperando nodos...", color_titulo=st7789.CYAN)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        host, msg = en_now.recv(50)
        if msg:
            try:
                data = json.loads(msg.decode())
                if data.get("type") == "FEEDBACK":
                    nodo = data.get("id", "UNKNOWN")
                    if nodo not in nodos_vistos:
                        nodos_vistos.append(nodo)
                    for medicion in data.get("payload", []):
                        publicar_dato(nodo, medicion["tipo"], medicion["val"])
                        pantalla_feedback(nodo, medicion["tipo"], medicion["val"])
                        utime.sleep_ms(300)
                        recibidos += 1
            except Exception as e:
                print("[ESP-NOW] Error parseando:", e)

    resumen = "{} lecturas".format(recibidos)
    pantalla_estado("LISTO", resumen, "{} nodos".format(len(nodos_vistos)), color_titulo=st7789.GREEN)
    utime.sleep_ms(1200)

# ───────────────────────────────────────────────
#  BOTONES
#  btn.left  (GPIO0)  → Forzar REQ:ALL manual
#  btn.right (GPIO35) → Mostrar lista de nodos vistos
# ───────────────────────────────────────────────
def revisar_botones():
    if btn.left.value() == 0:
        utime.sleep_ms(50)           # Debounce
        if btn.left.value() == 0:
            pantalla_estado("MANUAL", "REQ:ALL", color_titulo=st7789.YELLOW)
            utime.sleep_ms(300)
            init_espnow()
            medir_maestro()
            enviar_onda_pif("REQ:ALL")
            escuchar_feedback()
            en_now.active(False)

    if btn.right.value() == 0:
        utime.sleep_ms(50)
        if btn.right.value() == 0:
            pantalla_nodos()
            utime.sleep_ms(2000)

# ───────────────────────────────────────────────
#  CICLO PRINCIPAL
# ───────────────────────────────────────────────
def ciclo():
    global comando_pendiente

    revisar_botones()   # Chequear botones al despertar

    # 1. WiFi + MQTT
    if not conectar_wifi():
        pantalla_sleep()
        machine.lightsleep(SLEEP_MS)
        return

    if not conectar_mqtt():
        pantalla_sleep()
        machine.lightsleep(SLEEP_MS)
        return

    # 2. Revisar MQTT (no bloqueante)
    try:
        client.check_msg()
    except Exception as e:
        print("[MQTT] check_msg error:", e)

    # 3. Ejecutar comando si llegó
    if comando_pendiente:
        orden = comando_pendiente
        comando_pendiente = None
        init_espnow()
        procesar_comando(orden)
        escuchar_feedback()
        en_now.active(False)
    else:
        pantalla_estado(
            "EN ESPERA",
            "Nodos: {}".format(len(nodos_vistos)),
            "< REQ:ALL   nodos >",
            color_titulo=st7789.CYAN
        )

    # 4. Desconectar limpiamente
    try:
        client.disconnect()
    except:
        pass
    desconectar_wifi()

    # 5. Lightsleep
    pantalla_sleep()
    print("[Sleep] Durmiendo {}s...".format(SLEEP_MS // 1000))
    machine.lightsleep(SLEEP_MS)
    print("[Wake]  Despertando...")

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
pantalla_bienvenida()
utime.sleep_ms(2000)

while True:
    ciclo()
