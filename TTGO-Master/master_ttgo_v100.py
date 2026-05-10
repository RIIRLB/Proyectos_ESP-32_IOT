# ============================================================
#  MASTER_TTGO v10.0 — PIF Mesh / LAB-ARTE
#
#  Base de radio: v7.2 (probada y funcional)
#    WiFi  → sta.active(True) → sta.disconnect() → ESP-NOW
#    ESP-NOW → en.active(False) → sta.connect() → WiFi
#    NUNCA sta.active(False)
#
#  Arquitectura de energía:
#    ┌─ lightsleep 5s ─────────────────────────────────────┐
#    │  al despertar:                                       │
#    │    • parpadeo 50ms (heartbeat)                       │
#    │    • revisa flags IRQ de botones                     │
#    │    • si flag o 10min cumplidos → ciclo completo      │
#    │    • vuelve a lightsleep                             │
#    └─────────────────────────────────────────────────────┘
#
#  Ciclo completo (display ON siempre):
#    mide → ESP-NOW (WAVE + escucha 5s) → WiFi+MQTT → sleep
#
#  Botones con IRQ:
#    BTN35 (GPIO35) → ciclo completo inmediato
#    BTN0  (GPIO0)  → solo ventana MESH inmediata
#
#  Comandos desde Raspi vía MQTT:
#    "REQ:ALL"      → WAVE a todos los slaves
#    "REQ:SLAVE_01" → WAVE solo a ese slave (target específico)
#
#  CSV agrupado: T y H del mismo nodo en una sola línea
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
#MQTT_BROKER   = "192.168.1.146"
WIFI_SSID     = "Totalplay-C5AC"
WIFI_PASS     = "C5AC642BDVePRn6Z"
MQTT_BROKER   = "192.168.100.132"

CLIENT_ID     = "MASTER_TTGO_GATEWAY"
TOPIC_SUB     = b"comandos/mesh"
TOPIC_PUB     = b"datos/sensores"
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

SLEEP_CORTO_MS = 5_000    # Lightsleep entre checks
T_WIFI_MS      = 8_000    # Timeout conexión WiFi
T_MESH_MS      = 5_000    # Ventana escucha ESP-NOW
BROADCAST_N    = 3        # Repeticiones de WAVE
CICLOS_10MIN   = 120      # 120 × 5s = 600s = 10 min

# ───────────────────────────────────────────────
#  COLAS
# ───────────────────────────────────────────────
cola_subida = []   # datos malla → Raspberry (CSV)
cola_bajada = []   # órdenes Raspi → malla (JSON WAVE)

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
#  FLAGS de interrupción — solo se setean en ISR,
#  se leen y limpian en el loop principal
# ───────────────────────────────────────────────
_flag_completo = False   # BTN35 → ciclo completo
_flag_mesh     = False   # BTN0  → solo mesh

def _isr_completo(pin):
    global _flag_completo
    _flag_completo = True

def _isr_mesh(pin):
    global _flag_mesh
    _flag_mesh = True

btn_env = Pin(35, Pin.IN, Pin.PULL_UP)
btn_dir = Pin(0,  Pin.IN, Pin.PULL_UP)
btn_env.irq(trigger=Pin.IRQ_FALLING, handler=_isr_completo)
btn_dir.irq(trigger=Pin.IRQ_FALLING, handler=_isr_mesh)

# ───────────────────────────────────────────────
#  UI
# ───────────────────────────────────────────────
_ui_t   = "--"
_ui_h   = "--"
_ui_wif = None
_ui_mq  = None
_ui_svr = None

def _est(val):
    if val is True:  return "OK",  VERDE
    if val is False: return "ERR", ROJO
    return                   "---", GRIS

def ui_dash(msg="", col=None, t=None, h=None, wif=None, mq=None, svr=None):
    """Dibuja dashboard completo. Actualiza solo los campos que se pasan."""
    global _ui_t, _ui_h, _ui_wif, _ui_mq, _ui_svr
    if t   is not None: _ui_t   = t
    if h   is not None: _ui_h   = h
    if wif is not None: _ui_wif = wif
    if mq  is not None: _ui_mq  = mq
    if svr is not None: _ui_svr = svr

    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    ws, wc   = _est(_ui_wif)
    ms, mc   = _est(_ui_mq)
    svs, svc = _est(_ui_svr)

    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER",              4,   2,  VERDE)
    tft.write(font_sm, hora,                      160, 2,  CYAN)
    tft.write(font_md, "T: {}C".format(_ui_t),    4,   24, AMARILLO)
    tft.write(font_md, "H: {}%".format(_ui_h),    4,   54, CYAN)
    if msg:
        tft.write(font_sm, msg[:26],              4,   84, col if col else BLANCO)
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO); tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO); tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO); tft.write(font_sm, svs, 200, 108, svc)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v10.0",        4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)

def heartbeat():
    """Parpadeo corto — señal de vida sin despertar el display completo."""
    backlight.value(1)
    utime.sleep_ms(50)
    backlight.value(0)

# ───────────────────────────────────────────────
#  SENSORES
# ───────────────────────────────────────────────
def medir_propio():
    """Lee DHT11 con reintentos. Retorna (t, h) o ("Err","Err")."""
    for _ in range(3):
        t, h = hw.leer_dht()
        if t != "Error":
            return t, h
        utime.sleep_ms(1000)
    return "Err", "Err"

# ───────────────────────────────────────────────
#  CSV — T y H del mismo nodo en una sola línea
# ───────────────────────────────────────────────
def _payload_csv(hora, nodo, payload):
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
        lineas.append("{},{},T:{} H:{},sensor".format(
            hora, nodo,
            "T:{}".format(t_val) if t_val is not None else "T:?",
            "H:{}".format(h_val) if h_val is not None else "H:?"
        ))
    for tipo, val in otros:
        lineas.append("{},{},{},{}".format(hora, nodo, tipo, val))
    return lineas

# ───────────────────────────────────────────────
#  VENTANA MESH — igual que v7.2 pero con WAVE siempre
#
#  cmd puede ser:
#    "REQ:ALL"      → broadcast a todos
#    "REQ:SLAVE_01" → solo a ese slave (target específico)
# ───────────────────────────────────────────────
def ventana_mesh(cmd="REQ:ALL", display=True):
    if display: ui_dash("Iniciando MESH...", AMARILLO)

    # ── Radio: igual que v7.2 ──────────────────
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=6)

    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    # ── Determinar target ──────────────────────
    if cmd.startswith("REQ:") and cmd != "REQ:ALL":
        target = cmd[4:]   # "REQ:SLAVE_01" → "SLAVE_01"
    else:
        target = "ALL"

    # ── Procesar cola_bajada primero ───────────
    # (órdenes que llegaron de la Raspi en la última ventana WiFi)
    ondas = list(cola_bajada)   # copia
    cola_bajada.clear()
    if not ondas:
        ondas = [json.dumps({
            "type"  : "WAVE",
            "cmd"   : cmd,
            "from"  : CLIENT_ID,
            "target": target,
            "ttl"   : 6
        })]

    # ── Enviar WAVE(s) ─────────────────────────
    for onda in ondas:
        if display: ui_dash("Enviando WAVE...", AMARILLO)
        for _ in range(BROADCAST_N):
            try: en.send(BROADCAST_MAC, onda)
            except Exception as e: print("[TX ERR]", e)
            utime.sleep_ms(150)
        print("[MESH TX]", onda[:50])

    # ── Escuchar FBs ───────────────────────────
    if display: ui_dash("Escuchando {}ms...".format(T_MESH_MS), CYAN)
    fin = utime.ticks_add(utime.ticks_ms(), T_MESH_MS)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        try:
            host, msg = en.recv(10)
        except:
            utime.sleep_ms(10)
            continue
        if not msg:
            continue
        print("[MESH RX]", msg)
        try:
            txt  = msg.decode()
            data = json.loads(txt)
            if data.get("type") in ("FEEDBACK", "FB"):
                nodo    = data.get("id", "?")
                payload = data.get("payload") or data.get("pl") or []
                hora    = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
                for linea in _payload_csv(hora, nodo, payload):
                    cola_subida.append(linea)
                recibidos += 1
                if display:
                    ui_dash("Nodo: " + nodo, VERDE)
                    utime.sleep_ms(300)
            del data, txt
        except Exception as e:
            print("[RX ERR]", e)

    if display:
        ui_dash("Mesh OK nodos:{}".format(recibidos), CYAN)
        utime.sleep_ms(500)

    # ── Transición: igual que v7.2 ─────────────
    en.active(False)
    utime.sleep_ms(200)
    gc.collect()

# ───────────────────────────────────────────────
#  VENTANA WIFI+MQTT — igual que v7.2
# ───────────────────────────────────────────────
def _mqtt_cb(topic, msg):
    """
    Recibe comando de la Raspi y lo encola.
    Formato esperado: "REQ:ALL" o "REQ:SLAVE_01"
    """
    txt = msg.decode().strip()
    print("[MQTT RX]", txt)
    cola_bajada.append(json.dumps({
        "type"  : "WAVE",
        "cmd"   : txt,
        "from"  : CLIENT_ID,
        "target": txt[4:] if txt.startswith("REQ:") and txt != "REQ:ALL" else "ALL",
        "ttl"   : 6
    }))

def ventana_wifi(display=True):
    """
    Conecta al AP, sube cola_subida, baja comandos, desconecta.
    Retorna True si llegó a conectar WiFi.
    """
    if display: ui_dash("Conectando WiFi...", AMARILLO, wif=None, mq=None, svr=None)

    # ── Radio: igual que v7.2 ──────────────────
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=6)

    if not sta.isconnected():
        sta.connect(WIFI_SSID, WIFI_PASS)
        fin = utime.ticks_add(utime.ticks_ms(), T_WIFI_MS)
        while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
            if sta.isconnected(): break
            utime.sleep_ms(400)

    if not sta.isconnected():
        if display: ui_dash("WiFi sin conexion", ROJO, wif=False, mq=False, svr=False)
        utime.sleep_ms(600)
        try: sta.disconnect()
        except: pass
        utime.sleep_ms(200)
        return False

    if display: ui_dash("WiFi " + sta.ifconfig()[0], VERDE, wif=True)

    client = None
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(_mqtt_cb)
        client.connect()
        if display: ui_dash("MQTT OK", VERDE, mq=True)

        client.subscribe(TOPIC_SUB)
        client.check_msg()   # comandos Raspi → cola_bajada

        enviados = 0
        while cola_subida:
            item = cola_subida.pop(0)
            client.publish(TOPIC_PUB, item)
            enviados += 1
            print("[MQTT TX]", item)

        if display: ui_dash("Sync OK env:{}".format(enviados), VERDE, svr=True)
        utime.sleep_ms(400)

    except Exception as e:
        print("[MQTT ERR]", e)
        if display: ui_dash("MQTT ERR " + str(e)[:14], ROJO, mq=False, svr=False)
        utime.sleep_ms(600)
    finally:
        if client:
            try: client.disconnect()
            except: pass
            del client
        gc.collect()

    # ── Transición: igual que v7.2 ─────────────
    try: sta.disconnect()
    except: pass
    utime.sleep_ms(200)
    return True

# ───────────────────────────────────────────────
#  CICLO COMPLETO
# ───────────────────────────────────────────────
def ciclo_completo(display=True):
    """
    Mide → MESH (WAVE+escucha) → WiFi+MQTT
    display=True  → backlight ON, muestra todo
    display=False → silencioso (heartbeat ya lo hizo el loop)
    """
    if display:
        backlight.value(1)
        ui_dash("Midiendo sensor...", AMARILLO)

    # 1. Medir propio
    t, h = medir_propio()
    hora  = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
    if t != "Err":
        cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))
        if display: ui_dash(t=t, h=h, msg="Sensor OK", col=VERDE)
    else:
        if display: ui_dash(msg="DHT11 sin resp.", col=ROJO)
    utime.sleep_ms(300)

    # 2. MESH primero — los slaves tienen tiempo de responder
    #    mientras después subimos los datos al servidor
    try:
        ventana_mesh(display=display)
    except Exception as e:
        print("[MESH ERR]", e)
        if display: ui_dash("Mesh ERR " + str(e)[:13], ROJO)
        utime.sleep_ms(500)
        gc.collect()

    # 3. WiFi + MQTT
    try:
        ventana_wifi(display=display)
    except Exception as e:
        print("[WIFI ERR]", e)
        if display: ui_dash("WiFi ERR " + str(e)[:13], ROJO)
        utime.sleep_ms(500)
        gc.collect()

    if display:
        ui_dash("Durmiendo {}s...".format(SLEEP_CORTO_MS // 1000), GRIS)
        utime.sleep_ms(800)
        backlight.value(0)

    gc.collect()
    print("[RAM libre]", gc.mem_free())

# ───────────────────────────────────────────────
#  LOOP PRINCIPAL
#
#  Cada 5s: hearbeat → revisa flags → duerme
#  Cada 10 min (120 ciclos): ciclo_completo silencioso
#  Botón BTN35: ciclo_completo con display
#  Botón BTN0 : solo ventana_mesh con display
# ───────────────────────────────────────────────
def loop():
    global _flag_completo, _flag_mesh

    contador = CICLOS_10MIN   # arrancar con ciclo inmediato

    while True:

        # ── ¿Ejecutar algo? ────────────────────
        es_hora    = contador >= CICLOS_10MIN
        btn_compl  = _flag_completo
        btn_mesh   = _flag_mesh

        if btn_compl or btn_mesh or es_hora:
            backlight.value(1)

            if btn_mesh and not btn_compl and not es_hora:
                # Solo mesh (botón BTN0)
                _flag_mesh = False
                ui_dash("Boton: MESH", AMARILLO)
                utime.sleep_ms(200)
                try:
                    ventana_mesh(display=True)
                except Exception as e:
                    print("[MESH ERR]", e)
                    ui_dash("Mesh ERR " + str(e)[:13], ROJO)
                    gc.collect()
                utime.sleep_ms(500)
                backlight.value(0)

            else:
                # Ciclo completo (timer o BTN35)
                _flag_completo = False
                _flag_mesh     = False
                display = True   # display siempre ON en ciclo completo

                if btn_compl:
                    ui_dash("Boton: CICLO", AMARILLO)
                    utime.sleep_ms(200)
                elif es_hora:
                    ui_dash("Ciclo 10min", CYAN)
                    utime.sleep_ms(200)

                ciclo_completo(display=display)

            if es_hora:
                contador = 0
        else:
            # ── Heartbeat: parpadeo suave ───────
            backlight.value(1)
            utime.sleep_ms(50)
            backlight.value(0)

        # ── Lightsleep 5s ──────────────────────
        contador += 1
        print("[Sleep 5s] ciclo {}/{} | RAM {}".format(
            contador, CICLOS_10MIN, gc.mem_free()))
        machine.lightsleep(SLEEP_CORTO_MS)
        print("[Wake]")

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
utime.sleep_ms(2000)
gc.collect()

while True:
    try:
        loop()
    except Exception as e:
        print("[LOOP FATAL]", e)
        tft.fill(NEGRO)
        tft.write(font_md, "ERROR",       4, 8,  ROJO)
        tft.write(font_sm, str(e)[:26],   4, 50, BLANCO)
        tft.write(font_sm, "reiniciando", 4, 76, AMARILLO)
        backlight.value(1)
        utime.sleep_ms(5000)
        gc.collect()
