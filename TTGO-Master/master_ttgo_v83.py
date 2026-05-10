# ============================================================
#  MASTER_TTGO v8.3 — PIF Mesh / LAB-ARTE
#
#  Fix v8.1 — "WiFi Unknown Error 0x0101 / duplicate key":
#   [FIX-WIFI] network.WLAN() se llama UNA SOLA VEZ al arrancar
#              como _sta global. Jamás se vuelve a llamar.
#   [FIX-WIFI] _sta.active(False) NUNCA se llama — destruye el
#              netif y al recrearlo da "duplicate key".
#   [FIX-WIFI] _sta.active(True) solo al arranque. Después solo
#              _sta.connect() y _sta.disconnect().
#   [FIX-MEM]  fase_malla(): del en + gc.collect() al cerrar
#              ESP-NOW para liberar los ~30 KB del driver.
#
#  Flujo correcto de radio por ciclo:
#    [radio siempre ON desde el arranque]
#    _sta.connect()      → asociarse al AP
#    MQTT TX/RX
#    _sta.disconnect()   → soltar AP, radio sigue ON
#    ESPNow() + active(True) → ESP-NOW usa el radio
#    en.active(False) + del en + gc.collect()
#    [siguiente ciclo: volver a _sta.connect()]
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
#WIFI_SSID     = "Arte_Tenda2.4"
#WIFI_PASS     = "Lab4rt3#"
#MQTT_BROKER   =	"192.168.1.146"
WIFI_SSID     = "Totalplay-C5AC"
WIFI_PASS     = "C5AC642BDVePRn6Z"
MQTT_BROKER   = "192.168.100.132"
CLIENT_ID     = "MASTER_TTGO_GATEWAY"
TOPIC_SUB     = b"comandos/mesh"
TOPIC_PUB     = b"datos/sensores"
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

SLEEP_MS      = 15_000
ESCUCHA_MS    = 5_000
BROADCAST_N   = 3

# ───────────────────────────────────────────────
#  WLAN GLOBAL — creada UNA SOLA VEZ
#  Llamar network.WLAN() más de una vez → "duplicate key" → 0x0101
# ───────────────────────────────────────────────
_sta = network.WLAN(network.STA_IF)
_sta.active(True)      # Radio ON — permanece así todo el tiempo
_sta.config(channel=6)

# ───────────────────────────────────────────────
#  COLAS
# ───────────────────────────────────────────────
cola_subida = []
cola_bajada = []

# ───────────────────────────────────────────────
#  ESTADO GLOBAL — persiste entre fases
# ───────────────────────────────────────────────
estado = {
    "t"      : "--",
    "h"      : "--",
    "wifi"   : None,
    "mqtt"   : None,
    "server" : None,
    "msg"    : "Iniciando...",
    "msg_col": 0xFFFF,   # BLANCO como constante, aún no tenemos st7789
}

# ───────────────────────────────────────────────
#  HARDWARE
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
hw        = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(1)

from machine import Pin as _Pin
import esp32 as _esp32
_esp32.wake_on_ext0(pin=_Pin(0, _Pin.IN, _Pin.PULL_UP), level=_esp32.WAKEUP_ALL_LOW)

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

estado["msg_col"] = BLANCO   # ahora sí tenemos el valor correcto

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
def _estado_str(val):
    if val is True:  return ("OK",  VERDE)
    if val is False: return ("ERR", ROJO)
    return                   ("---", GRIS)

def ui_dash(msg="", col=None):
    if msg:
        estado["msg"]     = msg
        estado["msg_col"] = col if col else BLANCO

    h, m, s = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(h, m, s)

    w_str,  w_col  = _estado_str(estado["wifi"])
    mq_str, mq_col = _estado_str(estado["mqtt"])
    sv_str, sv_col = _estado_str(estado["server"])

    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER", 4,   2,  VERDE)
    tft.write(font_sm, hora,         160, 2,  CYAN)
    tft.write(font_md, "T: {}C".format(estado["t"]),  4, 24, AMARILLO)
    tft.write(font_md, "H: {}%".format(estado["h"]),  4, 54, CYAN)
    tft.write(font_sm, estado["msg"][:26],             4, 84, estado["msg_col"])
    tft.write(font_sm, "WiFi:",  4,   108, BLANCO)
    tft.write(font_sm, w_str,    54,  108, w_col)
    tft.write(font_sm, "MQTT:",  88,  108, BLANCO)
    tft.write(font_sm, mq_str,   140, 108, mq_col)
    tft.write(font_sm, "Svr:",   174, 108, BLANCO)
    tft.write(font_sm, sv_str,   210, 108, sv_col)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v8.1",         4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)

# ───────────────────────────────────────────────
#  FASE 1 — WiFi + MQTT
#  Usa _sta global — NUNCA crea un WLAN() nuevo
# ───────────────────────────────────────────────
def _mqtt_cb(topic, msg):
    print("[MQTT RX]", msg)
    paquete = json.dumps({
        "type"  : "WAVE",
        "cmd"   : msg.decode().strip(),
        "from"  : CLIENT_ID,
        "target": "ALL",
        "ttl"   : 6
    })
    cola_bajada.append(paquete)

def fase_wifi():
    estado["wifi"]   = None
    estado["mqtt"]   = None
    estado["server"] = None

    # _sta ya está active(True) desde el arranque — NO volver a llamar active()
    if not _sta.isconnected():
        ui_dash("Conectando WiFi...", AMARILLO)
        _sta.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(20):
            if _sta.isconnected(): break
            utime.sleep_ms(500)

    if not _sta.isconnected():
        estado["wifi"] = False
        ui_dash("WiFi sin conexion", ROJO)
        utime.sleep_ms(800)
        # No llamar active(False) — solo disconnect() para limpiar el intento
        try: _sta.disconnect()
        except: pass
        utime.sleep_ms(200)
        return False

    estado["wifi"] = True
    ui_dash("WiFi: " + _sta.ifconfig()[0], VERDE)

    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(_mqtt_cb)
        client.connect()
        estado["mqtt"] = True
        ui_dash("MQTT conectado", VERDE)

        client.subscribe(TOPIC_SUB)
        client.check_msg()   # órdenes Raspi → cola_bajada

        enviados = 0
        while cola_subida:
            item = cola_subida.pop(0)
            client.publish(TOPIC_PUB, item)
            enviados += 1
            print("[MQTT TX]", item)

        estado["server"] = True
        ui_dash("Sync OK  env:{}".format(enviados), CYAN)
        utime.sleep_ms(500)
        client.disconnect()
        del client

    except Exception as e:
        if estado["mqtt"] is None:
            estado["mqtt"] = False
        estado["server"] = False
        print("[MQTT ERROR]", e)
        ui_dash("MQTT ERR:" + str(e)[:16], ROJO)
        utime.sleep_ms(800)

    # Soltar el AP — radio queda ON para ESP-NOW
    try: _sta.disconnect()
    except: pass
    utime.sleep_ms(250)   # Pausa para que el driver procese el disconnect
    gc.collect()
    return True

# ───────────────────────────────────────────────
#  FASE 2 — ESP-NOW (malla)
#  _sta sigue active(True) — ESP-NOW usa el mismo radio
# ───────────────────────────────────────────────
def fase_malla():
    gc.collect()   # Limpiar antes de alloc del driver ESP-NOW
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    wave_default = json.dumps({
        "type"  : "WAVE",
        "cmd"   : "REQ:ALL",
        "from"  : CLIENT_ID,
        "target": "ALL",
        "ttl"   : 6
    })

    # 1. Enviar WAVE(s) a la malla
    if cola_bajada:
        while cola_bajada:
            cmd = cola_bajada.pop(0)
            ui_dash("ONDA: " + cmd[:20], AMARILLO)
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

    # 2. Escuchar FBs de los esclavos
    ui_dash("Escuchando mesh...", CYAN)
    fin = utime.ticks_add(utime.ticks_ms(), ESCUCHA_MS)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        host, msg = en.recv(10)
        if not msg:
            continue
        print("[RX RAW]", msg)
        try:
            txt  = msg.decode()
            data = json.loads(txt)

            if data.get("type") in ["FEEDBACK", "FB"]:
                nodo    = data.get("id", "?")
                payload = data.get("payload") or data.get("pl", [])
                hora    = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])

                for m in payload:
                    tipo = m.get("tipo") or m.get("t")
                    val  = m.get("val")  or m.get("v")
                    cola_subida.append("{},{},{},{}".format(hora, nodo, tipo, val))

                recibidos += 1
                ui_dash("Nodo: " + nodo, VERDE)
                utime.sleep_ms(250)

            del data, txt
        except:
            pass

    ui_dash("Mesh OK  nodos:{}".format(recibidos), CYAN)
    utime.sleep_ms(400)

    # Cerrar ESP-NOW y liberar memoria del driver
    try:
        en.active(False)
    except:
        pass
    del en
    gc.collect()   # Recupera los ~30 KB del driver ESP-NOW
    utime.sleep_ms(200)

# ───────────────────────────────────────────────
#  BOTONES
# ───────────────────────────────────────────────
def revisar_botones():
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
    gc.collect()
    backlight.value(1)
    utime.sleep_ms(2000)   # DHT11 estabilizar tras lightsleep

    revisar_botones()
    encolar_medicion_propia()
    fase_wifi()
    fase_malla()

    ui_dash("Sig. ciclo: {}s".format(SLEEP_MS // 1000), CYAN)
    utime.sleep_ms(1000)
    backlight.value(0)

    print("--- LightSleep {}s | RAM: {} ---".format(
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
        ui_dash("ERROR: " + str(e)[:20], ROJO)
        gc.collect()
        utime.sleep_ms(4000)
