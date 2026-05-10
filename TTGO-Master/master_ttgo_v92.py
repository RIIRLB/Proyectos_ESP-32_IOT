# ============================================================
#  MASTER_TTGO v9.2 — PIF Mesh / LAB-ARTE
#
#  Causa raíz de 0x0101 / "rx buffer" / "duplicate key":
#    en.active(False) en esta versión de firmware desinicializa
#    parte del driver WiFi. Al intentar reconectar, los buffers
#    ya no coinciden y el netif no puede recrearse.
#
#  Solución definitiva:
#    _sta y _en se crean UNA SOLA VEZ al arranque.
#    NUNCA se llama active(False) en ninguno de los dos.
#    El switching WiFi ↔ ESP-NOW se hace SOLO con:
#      _sta.connect()    ← entrar a WiFi
#      _sta.disconnect() ← salir de WiFi, ESP-NOW usa el radio libre
#
#  Convivencia ESP-NOW + WiFi en ESP32:
#    Cuando _sta está conectado a un AP, el canal cambia al del AP.
#    Cuando _sta.disconnect(), volvemos al canal 6 (configurado al inicio).
#    Los slaves también usan canal 6 → todo coincide.
#
#  Botones con IRQ:
#    GPIO35 → fuerza ventana SERVER (WiFi+MQTT)
#    GPIO0  → fuerza ventana MESH  (ESP-NOW)
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

T_SERVER_MS   = 5_000   # Duración ventana WiFi/MQTT
T_MESH_MS     = 5_000   # Duración ventana ESP-NOW
T_CICLO_MS    = 20_000  # Período mínimo entre ciclos completos
BROADCAST_N   = 3       # Repeticiones de WAVE

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
#  RADIO — init único al arranque
#
#  REGLA: estas dos líneas son las ÚNICAS veces en todo el
#  programa que se toca el estado del driver de radio.
#  Después solo se usa connect()/disconnect() y send()/recv().
# ───────────────────────────────────────────────
_sta = network.WLAN(network.STA_IF)
_sta.active(True)
_sta.config(channel=6)
utime.sleep_ms(200)

_en = espnow.ESPNow()
_en.active(True)
_en.add_peer(BROADCAST_MAC)

# ───────────────────────────────────────────────
#  COLAS Y ESTADO
# ───────────────────────────────────────────────
cola_subida = []
cola_bajada = []

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
#  FLAGS DE IRQ
# ───────────────────────────────────────────────
_flag_server = False
_flag_mesh   = False

def _isr_server(pin):
    global _flag_server
    _flag_server = True

def _isr_mesh(pin):
    global _flag_mesh
    _flag_mesh = True

btn_env = Pin(35, Pin.IN, Pin.PULL_UP)
btn_dir = Pin(0,  Pin.IN, Pin.PULL_UP)
btn_env.irq(trigger=Pin.IRQ_FALLING, handler=_isr_server)
btn_dir.irq(trigger=Pin.IRQ_FALLING, handler=_isr_mesh)

# ───────────────────────────────────────────────
#  UI
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
    ws, wc   = _est(estado["wifi"])
    ms, mc   = _est(estado["mqtt"])
    svs, svc = _est(estado["server"])

    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER",                4,   2,  VERDE)
    tft.write(font_sm, hora,                        160, 2,  CYAN)
    tft.write(font_md, "T: {}C".format(estado["t"]), 4,  24, AMARILLO)
    tft.write(font_md, "H: {}%".format(estado["h"]), 4,  54, CYAN)
    tft.write(font_sm, estado["msg"][:26],            4,  84, estado["msg_col"])
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO); tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO); tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO); tft.write(font_sm, svs, 200, 108, svc)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v9.2",         4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)

# ───────────────────────────────────────────────
#  SENSORES
# ───────────────────────────────────────────────
def encolar_medicion_propia():
    t, h = "Error", "Error"
    for i in range(3):
        t, h = hw.leer_dht()
        if t != "Error":
            break
        ui_dash("DHT retry {}/3".format(i + 1), AMARILLO)
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
#  CSV — T y H agrupados en una línea
# ───────────────────────────────────────────────
def _payload_a_csv(hora, nodo, payload):
    t_val = h_val = None
    otros = []
    for m in payload:
        tipo = m.get("t") or m.get("tipo", "?")
        val  = m.get("v") if m.get("v") is not None else m.get("val", "?")
        if   tipo == "Temp": t_val = val
        elif tipo == "Hum" : h_val = val
        else                : otros.append((tipo, val))

    lineas = []
    if t_val is not None or h_val is not None:
        ts = "T:{}".format(t_val) if t_val is not None else "T:?"
        hs = "H:{}".format(h_val) if h_val is not None else "H:?"
        lineas.append("{},{},{} {},sensor".format(hora, nodo, ts, hs))
    for tipo, val in otros:
        lineas.append("{},{},{},{}".format(hora, nodo, tipo, val))
    return lineas

# ───────────────────────────────────────────────
#  MQTT callback
# ───────────────────────────────────────────────
def _mqtt_cb(topic, msg):
    cola_bajada.append(json.dumps({
        "type": "WAVE", "cmd": msg.decode().strip(),
        "from": CLIENT_ID, "target": "ALL", "ttl": 6
    }))
    print("[MQTT RX]", msg)

# ───────────────────────────────────────────────
#  VENTANA SERVER — WiFi + MQTT
#
#  El radio ya está activo. Solo hacemos connect() y al final
#  disconnect() para liberar el canal para ESP-NOW.
# ───────────────────────────────────────────────
def ventana_server():
    global _flag_server
    _flag_server = False

    estado["wifi"] = estado["mqtt"] = estado["server"] = None
    ui_dash("Ventana SERVER...", AMARILLO)

    # ── Conectar al AP ─────────────────────────
    if not _sta.isconnected():
        _sta.connect(WIFI_SSID, WIFI_PASS)
        fin = utime.ticks_add(utime.ticks_ms(), T_SERVER_MS)
        while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
            if _sta.isconnected():
                break
            utime.sleep_ms(300)

    if not _sta.isconnected():
        estado["wifi"] = False
        ui_dash("WiFi sin conexion", ROJO)
        utime.sleep_ms(600)
        try: _sta.disconnect()
        except: pass
        utime.sleep_ms(200)
        return

    estado["wifi"] = True
    ui_dash("WiFi " + _sta.ifconfig()[0], VERDE)

    # Medir SIEMPRE, aunque MQTT falle después
    encolar_medicion_propia()

    # ── MQTT ───────────────────────────────────
    client = None
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(_mqtt_cb)
        client.connect()
        estado["mqtt"] = True

        client.subscribe(TOPIC_SUB)
        client.check_msg()

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
        print("[MQTT ERR]", e)
        ui_dash("MQTT ERR " + str(e)[:14], ROJO)
        utime.sleep_ms(600)
    finally:
        if client:
            try: client.disconnect()
            except: pass
            del client
        gc.collect()

    # Soltar el AP → el radio queda libre en canal 6 para ESP-NOW
    try: _sta.disconnect()
    except: pass
    utime.sleep_ms(300)

# ───────────────────────────────────────────────
#  VENTANA MESH — ESP-NOW
#
#  _en ya está activo desde el arranque. Solo send()/recv().
#  NO se llama active(False) — el driver nunca se toca.
# ───────────────────────────────────────────────
def ventana_mesh():
    global _flag_mesh
    _flag_mesh = False

    ui_dash("Ventana MESH...", AMARILLO)

    # WAVE a enviar
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
        try: _en.send(BROADCAST_MAC, wave)
        except Exception as e: print("[TX ERR]", e)
        utime.sleep_ms(150)
    del wave

    # ── Escuchar FBs ───────────────────────────
    ui_dash("Escuchando mesh...", CYAN)
    fin       = utime.ticks_add(utime.ticks_ms(), T_MESH_MS)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        try:
            host, msg = _en.recv(10)
        except:
            utime.sleep_ms(10)
            continue
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
            print("[RX ERR]", e)

    ui_dash("Mesh OK nodos:{}".format(recibidos), CYAN)
    utime.sleep_ms(300)
    gc.collect()

# ───────────────────────────────────────────────
#  LOOP PRINCIPAL
# ───────────────────────────────────────────────
def loop():
    global _flag_server, _flag_mesh

    t_ultimo_server = 0
    t_ultimo_mesh   = 0

    while True:
        ahora = utime.ticks_ms()

        # ── ¿Ejecutar ventana SERVER? ───────────
        forzar  = _flag_server
        vencido = utime.ticks_diff(ahora, t_ultimo_server) >= T_CICLO_MS

        if forzar or vencido:
            if forzar: ui_dash("Boton: SERVER", AMARILLO); utime.sleep_ms(200)
            backlight.value(1)
            try:
                ventana_server()
            except Exception as e:
                print("[SERVER ERR]", e)
                ui_dash("Svr ERR " + str(e)[:14], ROJO)
                utime.sleep_ms(1000)
                gc.collect()
            t_ultimo_server = utime.ticks_ms()

        # ── ¿Ejecutar ventana MESH? ─────────────
        ahora   = utime.ticks_ms()
        forzar  = _flag_mesh
        vencido = utime.ticks_diff(ahora, t_ultimo_mesh) >= T_CICLO_MS

        if forzar or vencido:
            if forzar: ui_dash("Boton: MESH", AMARILLO); utime.sleep_ms(200)
            backlight.value(1)
            try:
                ventana_mesh()
            except Exception as e:
                print("[MESH ERR]", e)
                ui_dash("Mesh ERR " + str(e)[:13], ROJO)
                utime.sleep_ms(1000)
                gc.collect()
            t_ultimo_mesh = utime.ticks_ms()

        # ── Dormir 1 s si no hay nada pendiente ─
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

# Primera iteración inmediata
_flag_server = True
_flag_mesh   = True

while True:
    try:
        loop()
    except Exception as e:
        print("[LOOP FATAL]", e)
        ui_dash("FATAL " + str(e)[:16], ROJO)
        gc.collect()
        utime.sleep_ms(5000)
