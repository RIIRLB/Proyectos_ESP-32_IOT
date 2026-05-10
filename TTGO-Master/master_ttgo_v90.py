# ============================================================
#  MASTER_TTGO v9.0 — PIF Mesh / LAB-ARTE
#
#  Arquitectura nueva:
#
#    ┌─────────────────────────────────────────────────────┐
#    │  LOOP PRINCIPAL (sin lightsleep entre ventanas)     │
#    │                                                     │
#    │  ┌──────────┐   ┌──────────┐   ┌────────────────┐  │
#    │  │ VENTANA  │→  │ VENTANA  │→  │   LIGHTSLEEP   │  │
#    │  │  SERVER  │   │  MESH    │   │   (opcional)   │  │
#    │  │  ~5 seg  │   │  ~5 seg  │   │                │  │
#    │  └──────────┘   └──────────┘   └────────────────┘  │
#    └─────────────────────────────────────────────────────┘
#
#    Botón GPIO35 (IRQ FALLING) → fuerza ventana SERVER ahora
#    Botón GPIO0  (IRQ FALLING) → fuerza ventana MESH ahora
#
#  Transición WiFi ↔ ESP-NOW (sin active(False)):
#    WiFi:    sta.connect()  →  MQTT  →  sta.disconnect()
#    ESP-NOW: ESPNow() + active(True)  →  malla  →  active(False) + del en
#    El radio STA permanece active(True) todo el tiempo.
#
#  CSV agrupado: T y H del mismo nodo en una sola línea.
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
WIFI_SSID      = "Arte_Tenda2.4"
WIFI_PASS      = "Lab4rt3#"
MQTT_BROKER    = "192.168.1.146"
CLIENT_ID      = "MASTER_TTGO_GATEWAY"
TOPIC_SUB      = b"comandos/mesh"
TOPIC_PUB      = b"datos/sensores"
BROADCAST_MAC  = b'\xff\xff\xff\xff\xff\xff'

T_SERVER_MS    = 5_000   # Duración ventana WiFi/MQTT
T_MESH_MS      = 5_000   # Duración ventana ESP-NOW
T_SLEEP_MS     = 10_000  # Lightsleep entre ciclos completos
BROADCAST_N    = 3       # Repeticiones de cada WAVE

# ───────────────────────────────────────────────
#  FLAGS DE INTERRUPCIÓN (ISR → loop principal)
#  Solo se escriben en la ISR, solo se leen+limpian en el loop.
#  No hacer operaciones pesadas dentro de la ISR.
# ───────────────────────────────────────────────
_flag_server = False   # True → ejecutar ventana SERVER cuanto antes
_flag_mesh   = False   # True → ejecutar ventana MESH cuanto antes

# ───────────────────────────────────────────────
#  COLAS
# ───────────────────────────────────────────────
cola_subida = []   # datos malla → Raspberry (CSV)
cola_bajada = []   # órdenes Raspi → malla (JSON WAVE)

# ───────────────────────────────────────────────
#  ESTADO GLOBAL — persiste entre ventanas
# ───────────────────────────────────────────────
estado = {
    "t"      : "--",
    "h"      : "--",
    "wifi"   : None,
    "mqtt"   : None,
    "server" : None,
    "msg"    : "Iniciando...",
    "msg_col": 0xFFFF,
}

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

estado["msg_col"] = BLANCO

# ───────────────────────────────────────────────
#  ISR — Interrupciones de botones
#  MicroPython: la ISR debe ser MUY corta.
#  Solo seteamos un flag booleano y salimos.
#  El debounce lo hace el loop principal.
# ───────────────────────────────────────────────
def _isr_server(pin):
    global _flag_server
    _flag_server = True

def _isr_mesh(pin):
    global _flag_mesh
    _flag_mesh = True

# GPIO35 = botón derecho → fuerza SERVER
# GPIO0  = botón izquierdo → fuerza MESH
btn_env = Pin(35, Pin.IN, Pin.PULL_UP)
btn_dir = Pin(0,  Pin.IN, Pin.PULL_UP)
btn_env.irq(trigger=Pin.IRQ_FALLING, handler=_isr_server)
btn_dir.irq(trigger=Pin.IRQ_FALLING, handler=_isr_mesh)

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

    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    ws, wc  = _est(estado["wifi"])
    ms, mc  = _est(estado["mqtt"])
    svs, svc = _est(estado["server"])

    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER",               4,   2,  VERDE)
    tft.write(font_sm, hora,                       160, 2,  CYAN)
    tft.write(font_md, "T: {}C".format(estado["t"]), 4, 24, AMARILLO)
    tft.write(font_md, "H: {}%".format(estado["h"]), 4, 54, CYAN)
    tft.write(font_sm, estado["msg"][:26],           4, 84, estado["msg_col"])
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO); tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO); tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO); tft.write(font_sm, svs, 200, 108, svc)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v9.0",         4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)

# ───────────────────────────────────────────────
#  WLAN helper — nunca llama active(False)
# ───────────────────────────────────────────────
def _get_sta():
    """
    network.WLAN(STA_IF) es singleton en MicroPython.
    Solo activamos si aún no está activo.
    NUNCA llamar active(False) → causa error 0x0101.
    """
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
        sta.config(channel=6)
        utime.sleep_ms(300)
    return sta

# ───────────────────────────────────────────────
#  SENSORES
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
        cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))
        ui_dash("Sensor T:{} H:{}".format(t, h), VERDE)
    else:
        ui_dash("DHT11 sin respuesta", ROJO)

    utime.sleep_ms(400)

# ───────────────────────────────────────────────
#  CSV helper — T y H en una sola línea
# ───────────────────────────────────────────────
def _payload_a_csv(hora, nodo, payload):
    t_val = None
    h_val = None
    otros = []
    for m in payload:
        tipo = m.get("t") or m.get("tipo", "?")
        val  = m.get("v") if m.get("v") is not None else m.get("val", "?")
        if tipo == "Temp": t_val = val
        elif tipo == "Hum": h_val = val
        else: otros.append((tipo, val))

    lineas = []
    if t_val is not None or h_val is not None:
        ts = "T:{}".format(t_val) if t_val is not None else "T:?"
        hs = "H:{}".format(h_val) if h_val is not None else "H:?"
        lineas.append("{},{},{} {},sensor".format(hora, nodo, ts, hs))
    for tipo, val in otros:
        lineas.append("{},{},{},{}".format(hora, nodo, tipo, val))
    return lineas

# ───────────────────────────────────────────────
#  VENTANA SERVER — WiFi + MQTT
# ───────────────────────────────────────────────
def _mqtt_cb(topic, msg):
    cola_bajada.append(json.dumps({
        "type": "WAVE", "cmd": msg.decode().strip(),
        "from": CLIENT_ID, "target": "ALL", "ttl": 6
    }))
    print("[MQTT RX]", msg)

def ventana_server():
    global _flag_server
    _flag_server = False   # limpiar flag de interrupción

    estado["wifi"] = estado["mqtt"] = estado["server"] = None
    ui_dash("Ventana SERVER...", AMARILLO)

    sta    = _get_sta()
    client = None

    # ── Conectar WiFi ──────────────────────────
    if not sta.isconnected():
        sta.connect(WIFI_SSID, WIFI_PASS)
        deadline = utime.ticks_add(utime.ticks_ms(), T_SERVER_MS)
        while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
            if sta.isconnected():
                break
            utime.sleep_ms(300)

    if not sta.isconnected():
        estado["wifi"] = False
        ui_dash("WiFi sin conexion", ROJO)
        utime.sleep_ms(600)
        try: sta.disconnect()
        except: pass
        utime.sleep_ms(200)
        return

    estado["wifi"] = True
    ui_dash("WiFi " + sta.ifconfig()[0], VERDE)

    # ── MQTT ───────────────────────────────────
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(_mqtt_cb)
        client.connect()
        estado["mqtt"] = True

        client.subscribe(TOPIC_SUB)
        client.check_msg()   # órdenes Raspi → cola_bajada

        encolar_medicion_propia()   # siempre medir antes de publicar

        enviados = 0
        while cola_subida:
            item = cola_subida.pop(0)
            client.publish(TOPIC_PUB, item)
            enviados += 1
            print("[MQTT TX]", item)

        estado["server"] = True
        ui_dash("Server OK env:{}".format(enviados), VERDE)
        utime.sleep_ms(400)

    except Exception as e:
        if estado["mqtt"] is None: estado["mqtt"] = False
        estado["server"] = False
        print("[MQTT ERROR]", e)
        ui_dash("MQTT ERR " + str(e)[:14], ROJO)
        utime.sleep_ms(600)
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

# ───────────────────────────────────────────────
#  VENTANA MESH — ESP-NOW
# ───────────────────────────────────────────────
def ventana_mesh():
    global _flag_mesh
    _flag_mesh = False   # limpiar flag de interrupción

    ui_dash("Ventana MESH...", AMARILLO)
    gc.collect()

    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    # WAVE a enviar (calculada una vez)
    if cola_bajada:
        wave = cola_bajada.pop(0)
        print("[TX WAVE custom]", wave[:40])
    else:
        wave = json.dumps({
            "type": "WAVE", "cmd": "REQ:ALL",
            "from": CLIENT_ID, "target": "ALL", "ttl": 6
        })
        print("[TX WAVE default] REQ:ALL")

    # ── Enviar WAVE ────────────────────────────
    ui_dash("Enviando WAVE...", AMARILLO)
    for _ in range(BROADCAST_N):
        en.send(BROADCAST_MAC, wave)
        utime.sleep_ms(150)
    del wave

    # ── Escuchar FBs durante T_MESH_MS ─────────
    ui_dash("Escuchando mesh...", CYAN)
    fin       = utime.ticks_add(utime.ticks_ms(), T_MESH_MS)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        host, msg = en.recv(10)
        if not msg:
            continue
        print("[RX]", msg)
        try:
            txt  = msg.decode()
            data = json.loads(txt)

            if data.get("type") in ("FEEDBACK", "FB"):
                nodo    = data.get("id", "?")
                payload = data.get("payload") or data.get("pl") or []
                hora    = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
                for linea in _payload_a_csv(hora, nodo, payload):
                    cola_subida.append(linea)
                recibidos += 1
                ui_dash("Nodo: " + nodo, VERDE)
                utime.sleep_ms(200)

            del data, txt
        except Exception as e:
            print("[RX ERROR]", e)

    ui_dash("Mesh OK nodos:{}".format(recibidos), CYAN)
    utime.sleep_ms(300)

    # Liberar driver ESP-NOW (~30 KB)
    try: en.active(False)
    except: pass
    del en
    gc.collect()
    utime.sleep_ms(200)

# ───────────────────────────────────────────────
#  LOOP PRINCIPAL
#
#  Flujo normal:
#    ventana_server() → ventana_mesh() → lightsleep(T_SLEEP_MS)
#
#  Flujo con botón (IRQ):
#    _flag_server → ventana_server() inmediato
#    _flag_mesh   → ventana_mesh()   inmediato
#    Ambas flags son aditivas: si se presionan los dos botones
#    antes de que el loop procese, se ejecutan las dos ventanas.
# ───────────────────────────────────────────────
def loop():
    global _flag_server, _flag_mesh

    t_ultimo_server = 0   # ticks del último server
    t_ultimo_mesh   = 0   # ticks del último mesh

    while True:
        ahora = utime.ticks_ms()

        # ── Decidir si ejecutar ventana SERVER ─────
        tiempo_server = utime.ticks_diff(ahora, t_ultimo_server)
        forzar_server = _flag_server   # leer flag (puede haber llegado por IRQ)

        if forzar_server or tiempo_server >= (T_SLEEP_MS + T_SERVER_MS + T_MESH_MS):
            if forzar_server:
                ui_dash("Boton: SERVER", AMARILLO)
                utime.sleep_ms(200)
            backlight.value(1)
            ventana_server()
            t_ultimo_server = utime.ticks_ms()
            gc.collect()

        # ── Decidir si ejecutar ventana MESH ───────
        ahora = utime.ticks_ms()
        tiempo_mesh = utime.ticks_diff(ahora, t_ultimo_mesh)
        forzar_mesh = _flag_mesh

        if forzar_mesh or tiempo_mesh >= (T_SLEEP_MS + T_SERVER_MS + T_MESH_MS):
            if forzar_mesh:
                ui_dash("Boton: MESH", AMARILLO)
                utime.sleep_ms(200)
            backlight.value(1)
            ventana_mesh()
            t_ultimo_mesh = utime.ticks_ms()
            gc.collect()

        # ── ¿Hay algo por hacer o dormimos? ────────
        # Si hay datos pendientes de subir → no dormir todavía
        if cola_subida and utime.ticks_diff(
                utime.ticks_ms(), t_ultimo_server) > 2000:
            # Hay datos frescos y ya pasaron 2 s desde el último server
            # → ir a server pronto sin esperar el ciclo completo
            continue

        # Lightsleep si ninguna ventana está pendiente
        # Usamos un sleep corto (1 s) para que las IRQ se puedan comprobar
        # (lightsleep en MicroPython no ejecuta código Python durante el sleep,
        #  pero los flags sí se setean antes de entrar y se comprueban al salir)
        if not _flag_server and not _flag_mesh:
            ui_dash("Esperando...", GRIS)
            backlight.value(0)
            machine.lightsleep(1000)
            backlight.value(1)

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
utime.sleep_ms(2000)
gc.collect()

# Forzar primera ejecución de ambas ventanas al arrancar
_flag_server = True
_flag_mesh   = True

while True:
    try:
        loop()
    except Exception as e:
        print("[ERROR loop]", e)
        ui_dash("ERROR " + str(e)[:18], ROJO)
        gc.collect()
        utime.sleep_ms(4000)
