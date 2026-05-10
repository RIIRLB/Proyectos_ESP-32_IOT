# ============================================================
#  MASTER_TTGO v8.4 — PIF Mesh / LAB-ARTE
#
#  Fix v8.2:
#   [FIX-WIFI-A] network.WLAN() es un singleton en MicroPython:
#                llamarlo N veces devuelve el MISMO objeto.
#                El error 0x0101 venía de active(False)+active(True)
#                → ahora NUNCA se llama active(False).
#   [FIX-WIFI-B] _get_sta() verifica active() antes de activar,
#                evita reinicializar el driver si ya está listo.
#   [FIX-WIFI-C] No se pone nada de red a nivel de módulo —
#                solo se toca el radio dentro de fase_wifi/malla.
#   [FIX-CSV]    T y H del mismo nodo van en UNA sola línea CSV:
#                "hora,nodo,T:23 H:65,MQ:412"  en vez de 3 filas.
#   [REVIEW]     Revisión completa: del en, gc.collect(), decode()
#                una vez, sin set(), del client, wave_default fuera
#                del bucle, manejo de error en disconnect().
#
#  Flujo de radio (nunca active(False)):
#    _get_sta() → connect() → MQTT → disconnect() → ESP-NOW → del en
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
#WIFI_SSID     = "Arte_Tenda2.4"
#WIFI_PASS     = "Lab4rt3#"
#MQTT_BROKER   =	"192.168.1.146"
WIFI_SSID     = "Totalplay-C5AC"
WIFI_PASS     = "C5AC642BDVePRn6Z"
MQTT_BROKER   = "192.168.100.132"
CLIENT_ID     = "MASTER_TTGO_R"
TOPIC_SUB     = b"comandos/mesh"
TOPIC_PUB     = b"datos/sensores"
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

SLEEP_MS      = 15_000
ESCUCHA_MS    = 5_000
BROADCAST_N   = 3

# ───────────────────────────────────────────────
#  COLAS
# ───────────────────────────────────────────────
cola_subida = []   # datos malla → Raspberry (CSV)
cola_bajada = []   # órdenes Raspi → malla (JSON)

# ───────────────────────────────────────────────
#  HARDWARE
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
hw        = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(1)

import esp32 as _esp32
_esp32.wake_on_ext0(pin=Pin(0, Pin.IN, Pin.PULL_UP), level=_esp32.WAKEUP_ALL_LOW)

# ───────────────────────────────────────────────
#  COLORES
# ───────────────────────────────────────────────
VERDE    = st7789.GREEN
ROJO     = st7789.RED
AMARILLO = st7789.YELLOW
CYAN     = st7789.CYAN
BLANCO   = st7789.WHITE
NEGRO    = st7789.BLACK
GRIS     = st7789.color565(80, 80, 80)

# ───────────────────────────────────────────────
#  ESTADO GLOBAL
# ───────────────────────────────────────────────
estado = {
    "t"      : "--",
    "h"      : "--",
    "wifi"   : None,
    "mqtt"   : None,
    "server" : None,
    "msg"    : "Iniciando...",
    "msg_col": BLANCO,
}

# ───────────────────────────────────────────────
#  WLAN helper — nunca llama active(False)
#
#  network.WLAN(STA_IF) es un singleton en MicroPython:
#  cada llamada devuelve el MISMO objeto interno.
#  El error 0x0101 "duplicate key" ocurre si haces:
#    active(False) → active(True)   ← destruye y recrea el netif
#  Solución: solo activar si aún no está activo.
# ───────────────────────────────────────────────
def _get_sta():
    """Devuelve el objeto WLAN STA, activándolo solo si hace falta."""
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
        sta.config(channel=6)
        utime.sleep_ms(300)   # pequeña pausa para que el driver arranque
    return sta

# ───────────────────────────────────────────────
#  UI — Dashboard
#  Layout (240×135):
#  ┌────────────────────────────────────┐
#  │  PIF MASTER          [hora]        │  y=2   font_sm
#  │  T: 23C                            │  y=24  font_md  GRANDE
#  │  H: 65%                            │  y=54  font_md  GRANDE
#  │  [mensaje de estado]               │  y=84  font_sm
#  │  WiFi:OK  MQTT:OK  Svr:OK          │  y=108 font_sm
#  └────────────────────────────────────┘
# ───────────────────────────────────────────────
def _est(val):
    if val is True:  return "OK",  VERDE
    if val is False: return "ERR", ROJO
    return                   "---", GRIS

def ui_dash(msg="", col=None):
    if msg:
        estado["msg"]     = msg
        estado["msg_col"] = col if col else BLANCO

    h, m, s = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(h, m, s)
    ws, wc  = _est(estado["wifi"])
    ms, mc  = _est(estado["mqtt"])
    ss, sc  = _est(estado["server"])

    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER",              4,   2,  VERDE)
    tft.write(font_sm, hora,                      160, 2,  CYAN)
    tft.write(font_md, "T: {}C".format(estado["t"]), 4, 24, AMARILLO)
    tft.write(font_md, "H: {}%".format(estado["h"]), 4, 54, CYAN)
    tft.write(font_sm, estado["msg"][:26],         4,  84, estado["msg_col"])
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO);  tft.write(font_sm, ws, 54,  108, wc)
    tft.write(font_sm, "MQTT:", 88,  108, BLANCO);  tft.write(font_sm, ms, 138, 108, mc)
    tft.write(font_sm, "Svr:",  172, 108, BLANCO);  tft.write(font_sm, ss, 208, 108, sc)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v8.4",         4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)

# ───────────────────────────────────────────────
#  FASE 1 — WiFi + MQTT
# ───────────────────────────────────────────────
def _mqtt_cb(topic, msg):
    """Recibe orden de la Raspi y la encola para la malla."""
    print("[MQTT RX]", msg)
    cola_bajada.append(json.dumps({
        "type"  : "WAVE",
        "cmd"   : msg.decode().strip(),
        "from"  : CLIENT_ID,
        "target": "ALL",
        "ttl"   : 6
    }))

def fase_wifi():
    estado["wifi"] = estado["mqtt"] = estado["server"] = None

    sta = _get_sta()   # singleton, nunca recrea el netif

    if not sta.isconnected():
        ui_dash("Conectando WiFi...", AMARILLO)
        sta.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(20):
            if sta.isconnected():
                break
            utime.sleep_ms(500)

    if not sta.isconnected():
        estado["wifi"] = False
        ui_dash("WiFi sin conexion", ROJO)
        utime.sleep_ms(800)
        try: sta.disconnect()
        except: pass
        utime.sleep_ms(200)
        return False

    estado["wifi"] = True
    ui_dash("WiFi OK " + sta.ifconfig()[0], VERDE)

    client = None
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(_mqtt_cb)
        client.connect()
        estado["mqtt"] = True
        ui_dash("MQTT OK", VERDE)

        client.subscribe(TOPIC_SUB)
        client.check_msg()   # órdenes Raspi → cola_bajada

        enviados = 0
        while cola_subida:
            item = cola_subida.pop(0)
            client.publish(TOPIC_PUB, item)
            enviados += 1
            print("[MQTT TX]", item)

        estado["server"] = True
        ui_dash("Sync OK env:{}".format(enviados), CYAN)
        utime.sleep_ms(500)

    except Exception as e:
        if estado["mqtt"] is None:
            estado["mqtt"] = False
        estado["server"] = False
        print("[MQTT ERROR]", e)
        ui_dash("MQTT ERR " + str(e)[:14], ROJO)
        utime.sleep_ms(800)
    finally:
        if client:
            try: client.disconnect()
            except: pass
            del client

    # Soltar AP — radio sigue ON para ESP-NOW
    try: sta.disconnect()
    except: pass
    utime.sleep_ms(250)
    gc.collect()
    return True

# ───────────────────────────────────────────────
#  FASE 2 — ESP-NOW (malla)
# ───────────────────────────────────────────────
def _payload_a_csv(hora, nodo, payload):
    """
    [FIX-CSV] Agrupa T y H del mismo nodo en una sola línea.
    Formato: "hora,nodo,T:23 H:65,MQ:412"
    Otros sensores van en líneas separadas si no hay T/H que agrupar con ellos.
    """
    t_val = None
    h_val = None
    otros = []

    for m in payload:
        tipo = m.get("t") or m.get("tipo", "?")
        val  = m.get("v") if m.get("v") is not None else m.get("val", "?")
        if tipo == "Temp":
            t_val = val
        elif tipo == "Hum":
            h_val = val
        else:
            otros.append((tipo, val))

    lineas = []

    # T y H → una sola línea
    if t_val is not None or h_val is not None:
        t_str = "T:{}".format(t_val) if t_val is not None else "T:?"
        h_str = "H:{}".format(h_val) if h_val is not None else "H:?"
        lineas.append("{},{},{} {},sensor".format(hora, nodo, t_str, h_str))

    # Resto de sensores → una línea cada uno
    for tipo, val in otros:
        lineas.append("{},{},{},{}".format(hora, nodo, tipo, val))

    return lineas

def fase_malla():
    gc.collect()
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    # WAVE por defecto (calculada una sola vez, fuera de cualquier bucle)
    wave_default = json.dumps({
        "type"  : "WAVE",
        "cmd"   : "REQ:ALL",
        "from"  : CLIENT_ID,
        "target": "ALL",
        "ttl"   : 6
    })

    # 1. Enviar WAVE(s) — siempre se envía al menos la default
    if cola_bajada:
        while cola_bajada:
            cmd = cola_bajada.pop(0)
            ui_dash("Onda: " + cmd[:18], AMARILLO)
            for _ in range(BROADCAST_N):
                en.send(BROADCAST_MAC, cmd)
                utime.sleep_ms(150)
            print("[TX WAVE custom]", cmd[:40])
    else:
        ui_dash("Onda REQ:ALL...", AMARILLO)
        for _ in range(BROADCAST_N):
            en.send(BROADCAST_MAC, wave_default)
            utime.sleep_ms(150)
        print("[TX WAVE default] REQ:ALL")

    del wave_default

    # 2. Escuchar FBs
    ui_dash("Escuchando mesh...", CYAN)
    fin = utime.ticks_add(utime.ticks_ms(), ESCUCHA_MS)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        host, msg = en.recv(10)
        if not msg:
            continue
        print("[RX RAW]", msg)
        try:
            txt  = msg.decode()        # decode() una sola vez
            data = json.loads(txt)

            if data.get("type") in ("FEEDBACK", "FB"):
                nodo    = data.get("id", "?")
                payload = data.get("payload") or data.get("pl") or []
                hora    = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])

                for linea in _payload_a_csv(hora, nodo, payload):
                    cola_subida.append(linea)

                recibidos += 1
                ui_dash("Nodo: " + nodo, VERDE)
                utime.sleep_ms(250)

            del data, txt
        except Exception as e:
            print("[RX ERROR]", e)

    ui_dash("Mesh OK nodos:{}".format(recibidos), CYAN)
    utime.sleep_ms(400)

    # Cerrar ESP-NOW y liberar RAM del driver (~30 KB)
    try: en.active(False)
    except: pass
    del en
    gc.collect()
    utime.sleep_ms(200)

# ───────────────────────────────────────────────
#  BOTONES
# ───────────────────────────────────────────────
def revisar_botones():
    if hw.btn_env.value() == 0:
        utime.sleep_ms(50)
        if hw.btn_env.value() == 0:
            ui_dash("Boton REQ:ALL", AMARILLO)
            encolar_medicion_propia()
            cola_bajada.append(json.dumps({
                "type": "WAVE", "cmd": "REQ:ALL",
                "from": CLIENT_ID, "target": "ALL", "ttl": 6
            }))
            utime.sleep_ms(300)

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
        ui_dash("DHT retry {}/3".format(intento + 1), AMARILLO)
        utime.sleep_ms(1000)

    hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])

    if t != "Error":
        estado["t"] = t
        estado["h"] = h
        # Master también usa el formato agrupado T/H en una línea
        cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))
        ui_dash("Sensor OK T:{} H:{}".format(t, h), VERDE)
    else:
        ui_dash("DHT11 sin respuesta", ROJO)

    utime.sleep_ms(600)

# ───────────────────────────────────────────────
#  CICLO PRINCIPAL
# ───────────────────────────────────────────────
def ciclo():
    gc.collect()
    backlight.value(1)
    utime.sleep_ms(2000)   # DHT11 necesita estabilizarse tras lightsleep

    revisar_botones()
    encolar_medicion_propia()
    fase_wifi()
    fase_malla()

    ui_dash("Prox ciclo {}s".format(SLEEP_MS // 1000), CYAN)
    utime.sleep_ms(1000)
    backlight.value(0)

    print("--- LightSleep {}s | RAM libre: {} ---".format(
        SLEEP_MS // 1000, gc.mem_free()))
    machine.lightsleep(SLEEP_MS)
    print("--- Despertando ---")

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
utime.sleep_ms(2000)
gc.collect()

while True:
    try:
        ciclo()
    except Exception as e:
        print("[ERROR ciclo]", e)
        ui_dash("ERROR " + str(e)[:18], ROJO)
        gc.collect()
        utime.sleep_ms(4000)
