# ============================================================
#  MASTER_TTGO v8 — PIF Mesh / LAB-ARTE
#
#  Correcciones v8.0:
#   [FIX-1] _mqtt_cb nunca encolaba en cola_bajada → corregido
#   [FIX-2] fase_malla() nunca enviaba WAVE si cola_bajada vacía
#            → ahora siempre envía REQ:ALL por defecto
#   [NEW]   UI persistente: temp/hum arriba, barra WiFi/MQTT/Server abajo
#
#  Flujo correcto de radio:
#    WiFi  → sta.disconnect() [radio ON, sin AP] → ESP-NOW
#    ESP-NOW → espnow.active(False) → sta.connect() → WiFi
#    NUNCA hacer sta.active(False) entre fases.
#
#  Colas:
#    cola_subida : datos de la malla → Raspberry Pi (MQTT TX)
#    cola_bajada : órdenes de la Raspi → malla (ESP-NOW TX)
# ============================================================

import gc, network, espnow, machine, utime, json
from machine import Pin
from umqtt.simple import MQTTClient
import tft_config
import st7789py as st7789
import comfortaa_16 as font_sm
import comfortaa_24 as font_md
from sens import Sensores

gc.collect()

# ───────────────────────────────────────────────
#  CONFIGURACIÓN
# ───────────────────────────────────────────────
WIFI_SSID     = "Arte_Tenda2.4"
WIFI_PASS     = "Lab4rt3#"
MQTT_BROKER   = "192.168.1.146"
CLIENT_ID     = "MASTER_TTGO_GATEWAY"
TOPIC_SUB     = b"comandos/mesh"
TOPIC_PUB     = b"datos/sensores"
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

SLEEP_MS      = 15_000   # Reposo entre ciclos (ms)
ESCUCHA_MS    = 5_000    # Ventana de escucha ESP-NOW (ms) — aumentado
BROADCAST_N   = 3        # Repeticiones de cada onda PIF

# ───────────────────────────────────────────────
#  COLAS (buzones entre fases)
# ───────────────────────────────────────────────
cola_subida = []   # Malla → Raspberry  (CSV listos para publicar)
cola_bajada = []   # Raspberry → Malla  (JSON listos para broadcast)

# ───────────────────────────────────────────────
#  ESTADO GLOBAL — persiste entre fases del ciclo
# ───────────────────────────────────────────────
estado = {
    "t"      : "--",    # Temperatura propia
    "h"      : "--",    # Humedad propia
    "wifi"   : None,    # True/False/None
    "mqtt"   : None,
    "server" : None,
    "msg"    : "Iniciando...",
    "msg_col": st7789.WHITE,
}

# ───────────────────────────────────────────────
#  HARDWARE
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
hw        = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(1)

# GPIO0 como wake desde lightsleep (botón izquierdo)
from machine import Pin as _Pin
import esp32 as _esp32
_esp32.wake_on_ext0(pin=_Pin(0, _Pin.IN, _Pin.PULL_UP), level=_esp32.WAKEUP_ALL_LOW)

# ───────────────────────────────────────────────
#  COLORES ALIAS
# ───────────────────────────────────────────────
VERDE    = st7789.GREEN
ROJO     = st7789.RED
AMARILLO = st7789.YELLOW
CYAN     = st7789.CYAN
BLANCO   = st7789.WHITE
NEGRO    = st7789.BLACK
GRIS     = st7789.color565(80, 80, 80)

# ───────────────────────────────────────────────
#  UI — Dashboard persistente
#
#  Layout (240×135):
#  ┌────────────────────────────────────┐
#  │  PIF MASTER            [hora]      │  y=4  font_md
#  │  T: 23°C   H: 65%                 │  y=36 font_sm
#  │  [mensaje de estado]               │  y=60 font_sm
#  │  WiFi:OK  MQTT:OK  Svr:OK          │  y=102 font_sm (barra)
#  └────────────────────────────────────┘
# ───────────────────────────────────────────────
def _estado_str(val):
    if val is True:  return ("OK",  VERDE)
    if val is False: return ("ERR", ROJO)
    return                   ("---", GRIS)

def ui_dash(msg="", col=None):
    """Redibuja el dashboard completo de una sola vez."""
    if msg:
        estado["msg"]     = msg
        estado["msg_col"] = col if col else BLANCO

    # Hora actual
    h, m, s = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(h, m, s)

    # Barra inferior
    w_str,  w_col  = _estado_str(estado["wifi"])
    mq_str, mq_col = _estado_str(estado["mqtt"])
    sv_str, sv_col = _estado_str(estado["server"])

    tft.fill(NEGRO)

    # Título + hora
    tft.write(font_md, "PIF MASTER", 4,  4,  VERDE)
    tft.write(font_sm, hora,         170, 10, CYAN)

    # Sensor propio
    sensor_txt = "T:{}C  H:{}%".format(estado["t"], estado["h"])
    tft.write(font_sm, sensor_txt, 4, 36, BLANCO)

    # Mensaje de estado actual
    tft.write(font_sm, estado["msg"][:26], 4, 60, estado["msg_col"])

    # Barra WiFi / MQTT / Server
    tft.write(font_sm, "WiFi:",  4,   102, BLANCO)
    tft.write(font_sm, w_str,    58,  102, w_col)
    tft.write(font_sm, "MQTT:",  92,  102, BLANCO)
    tft.write(font_sm, mq_str,   148, 102, mq_col)
    tft.write(font_sm, "Svr:",   182, 102, BLANCO)
    tft.write(font_sm, sv_str,   218, 102, sv_col)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER", 4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",   4,  44, CYAN)
    tft.write(font_sm, "v8.0",       4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4, 92, BLANCO)

# ───────────────────────────────────────────────
#  FASE 1 — WiFi + MQTT
# ───────────────────────────────────────────────
def _mqtt_cb(topic, msg):
    """
    [FIX-1] Antes este callback construía el paquete pero nunca
    lo metía en cola_bajada. Ahora sí lo encola.
    """
    print("[MQTT RX]", msg)
    paquete = json.dumps({
        "type"  : "WAVE",
        "cmd"   : msg.decode().strip(),
        "from"  : CLIENT_ID,
        "target": "ALL",
        "ttl"   : 6
    })
    cola_bajada.append(paquete)   # ← FIX: esta línea faltaba

def fase_wifi():
    estado["wifi"]   = None
    estado["mqtt"]   = None
    estado["server"] = None

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=6)

    if not sta.isconnected():
        ui_dash("Conectando WiFi...", AMARILLO)
        sta.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(20):
            if sta.isconnected(): break
            utime.sleep_ms(500)

    if not sta.isconnected():
        estado["wifi"] = False
        ui_dash("WiFi sin conexion", ROJO)
        utime.sleep_ms(800)
        sta.disconnect()
        utime.sleep_ms(200)
        return False

    estado["wifi"] = True
    ui_dash("WiFi: " + sta.ifconfig()[0], VERDE)

    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(_mqtt_cb)
        client.connect()
        estado["mqtt"] = True
        ui_dash("MQTT conectado", VERDE)

        client.subscribe(TOPIC_SUB)

        # Recibir órdenes de la Raspi → llenan cola_bajada
        client.check_msg()

        # Vaciar cola_subida → publicar datos de la malla
        enviados = 0
        while cola_subida:
            item = cola_subida.pop(0)
            client.publish(TOPIC_PUB, item)
            enviados += 1
            print("[MQTT TX]", item)

        estado["server"] = True
        ui_dash("Sync OK  env:{}".format(enviados), CYAN)
        utime.sleep_ms(600)

        client.disconnect()

    except Exception as e:
        if estado["mqtt"] is None:
            estado["mqtt"] = False
        estado["server"] = False
        print("[MQTT ERROR]", e)
        ui_dash("MQTT ERR:" + str(e)[:16], ROJO)
        utime.sleep_ms(800)

    # Transición clave: desconectar AP pero DEJAR RADIO ON
    sta.disconnect()
    utime.sleep_ms(200)
    return True

# ───────────────────────────────────────────────
#  FASE 2 — ESP-NOW (malla)
# ───────────────────────────────────────────────
def fase_malla():
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    # [FIX-2] Siempre enviar al menos una WAVE REQ:ALL por defecto.
    # Antes solo se enviaba si cola_bajada tenía elementos,
    # lo que dependía de _mqtt_cb (que tenía Bug #1).
    # Ahora se garantiza que los slaves siempre reciben una WAVE.
    wave_default = json.dumps({
        "type"  : "WAVE",
        "cmd"   : "REQ:ALL",
        "from"  : CLIENT_ID,
        "target": "ALL",
        "ttl"   : 6
    })

    # 1. Enviar órdenes de la Raspi (si hay) o la WAVE por defecto
    if cola_bajada:
        while cola_bajada:
            cmd = cola_bajada.pop(0)
            ui_dash("ONDA: " + cmd[:20], AMARILLO)
            for _ in range(BROADCAST_N):
                en.send(BROADCAST_MAC, cmd)
                utime.sleep_ms(150)
            print("[TX WAVE personalizada]", cmd[:40])
    else:
        # WAVE por defecto — siempre se envía
        ui_dash("Onda REQ:ALL...", AMARILLO)
        for _ in range(BROADCAST_N):
            en.send(BROADCAST_MAC, wave_default)
            utime.sleep_ms(150)
        print("[TX WAVE default] REQ:ALL")

    # 2. Escuchar feedback de los esclavos
    ui_dash("Escuchando mesh...", CYAN)
    fin = utime.ticks_add(utime.ticks_ms(), ESCUCHA_MS)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        host, msg = en.recv(10)
        if not msg:
            continue
        print("[RX RAW]", msg)
        try:
            raw  = msg.decode()
            data = json.loads(raw)

            if data.get("type") in ["FEEDBACK", "FB"]:
                nodo    = data.get("id", "?")
                payload = data.get("payload") or data.get("pl", [])

                hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])

                for m in payload:
                    tipo = m.get("tipo") or m.get("t")
                    val  = m.get("val")  or m.get("v")
                    csv  = "{},{},{},{}".format(hora, nodo, tipo, val)
                    cola_subida.append(csv)

                recibidos += 1
                ui_dash("Nodo: " + nodo, VERDE)
                utime.sleep_ms(300)

            # Ignorar silenciosamente otros tipos (WAVE ecos, etc.)

        except:
            pass

    ui_dash("Ciclo OK  nodos:{}".format(recibidos), CYAN)
    utime.sleep_ms(500)

    # Transición: desactivar ESP-NOW antes de volver a WiFi
    en.active(False)
    utime.sleep_ms(200)

# ───────────────────────────────────────────────
#  BOTONES
# ───────────────────────────────────────────────
def revisar_botones():
    # GPIO35 (btn_env) → forzar REQ:ALL inmediato
    if hw.btn_env.value() == 0:
        utime.sleep_ms(50)
        if hw.btn_env.value() == 0:
            ui_dash("Boton: REQ:ALL", AMARILLO)
            encolar_medicion_propia()
            cola_bajada.append(json.dumps({
                "type": "WAVE", "cmd": "REQ:ALL",
                "from": CLIENT_ID, "target": "ALL", "ttl": 6
            }))
            utime.sleep_ms(300)

    # GPIO0 (btn_dir) → mostrar cola pendiente
    if hw.btn_dir.value() == 0:
        utime.sleep_ms(50)
        if hw.btn_dir.value() == 0:
            ui_dash("Cola: {} items".format(len(cola_subida)), CYAN)
            utime.sleep_ms(2000)

# ───────────────────────────────────────────────
#  MEDICIÓN PROPIA DEL MASTER
# ───────────────────────────────────────────────
def encolar_medicion_propia():
    t, h = "Error", "Error"
    for intento in range(3):
        t, h = hw.leer_dht()
        if t != "Error":
            break
        ui_dash("DHT retry {}/3...".format(intento + 1), AMARILLO)
        utime.sleep_ms(1000)

    hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])

    if t != "Error":
        estado["t"] = t
        estado["h"] = h
        cola_subida.append("{},{},Temperatura,{}".format(hora, CLIENT_ID, t))
        cola_subida.append("{},{},Humedad,{}".format(hora, CLIENT_ID, h))
        ui_dash("Sensor propio OK", VERDE)
    else:
        ui_dash("DHT11 sin resp.", ROJO)

    utime.sleep_ms(600)

# ───────────────────────────────────────────────
#  CICLO PRINCIPAL
# ───────────────────────────────────────────────
def ciclo():
    backlight.value(1)
    utime.sleep_ms(2000)   # DHT11 estabilizar tras sleep

    revisar_botones()

    # 1. Medir sensor propio
    encolar_medicion_propia()

    # 2. WiFi + MQTT
    fase_wifi()

    # 3. ESP-NOW malla
    fase_malla()

    # 4. Dormir
    ui_dash("Sig. ciclo: {}s".format(SLEEP_MS // 1000), CYAN)
    utime.sleep_ms(1200)
    backlight.value(0)

    print("--- LightSleep {}s ---".format(SLEEP_MS // 1000))
    machine.lightsleep(SLEEP_MS)
    print("--- Despertando ---")

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
utime.sleep_ms(2000)

while True:
    try:
        ciclo()
    except Exception as e:
        print("[ERROR ciclo]", e)
        ui_dash("ERROR: " + str(e)[:20], ROJO)
        utime.sleep_ms(4000)
