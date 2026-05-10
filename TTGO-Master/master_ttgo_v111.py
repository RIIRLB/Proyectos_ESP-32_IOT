# ============================================================
#  MASTER_TTGO v11.1 — PIF Mesh / LAB-ARTE
#
#  Fix definitivo del error 0x0101:
#    WiFi y ESP-NOW COEXISTEN en el ESP32.
#    Se inicializan UNA SOLA VEZ al arranque.
#    NUNCA se llama active(False) en nada.
#    El switching es solo con connect()/disconnect():
#      sta.connect()    → modo WiFi (ESP-NOW sigue vivo en mismo canal)
#      sta.disconnect() → modo ESP-NOW (canal 6, sin AP)
#
#  Protección soft-reboot:
#    El driver C sobrevive al soft-reboot de Python.
#    Verificamos active() antes de activar para no reinicializar
#    algo que ya está inicializado → causa del 0x0101.
#
#  Heartbeat (parpadeo 80ms cada 5s):
#    Solo cuando el sistema está estable en el loop,
#    no durante operaciones de radio.
#
#  Botones (IRQ):
#    BTN35 (GPIO35) → mide propio + sube servidor ahora
#    BTN0  (GPIO0)  → WAVE a todos los slaves ahora
#
#  Pantalla completa cada 10 min (T/H grande + nodos + estado).
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
#WIFI_SSID   = "Arte_Tenda2.4"
#WIFI_PASS   = "Lab4rt3#"
#MQTT_BROKER = "192.168.1.146"
WIFI_SSID   = "Totalplay-C5AC"
WIFI_PASS   = "C5AC642BDVePRn6Z"
MQTT_BROKER = "192.168.100.132"

CLIENT_ID     = "MASTER_TTGO_GATEWAY"
TOPIC_SUB     = b"comandos/mesh"
TOPIC_PUB     = b"datos/sensores"
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

T_MESH_MS     = 5_000    # Ventana escucha ESP-NOW
T_WIFI_MS     = 8_000    # Timeout conexión WiFi
T_10MIN_MS    = 600_000  # 10 minutos
T_HEARTBEAT   = 5_000    # Parpadeo cada 5 segundos
BROADCAST_N   = 3        # Repeticiones de WAVE
DISPLAY_ON_S  = 15       # Segundos pantalla completa visible

# ───────────────────────────────────────────────
#  HARDWARE (no-radio)
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
hw        = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(0)

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
#  RADIO — init único, protegido para soft-reboot
#
#  En soft-reboot: el driver C sigue vivo pero Python
#  reinicia. Si llamamos active(True) en algo ya activo
#  → 0x0101. La solución: verificar antes.
#
#  REGLA ABSOLUTA después de aquí:
#    ✅ sta.connect() / sta.disconnect()
#    ✅ en.send() / en.recv()
#    ❌ sta.active(False) — NUNCA
#    ❌ en.active(False)  — NUNCA
#    ❌ network.WLAN()    — NUNCA de nuevo (singleton ya creado)
#    ❌ espnow.ESPNow()   — NUNCA de nuevo
# ───────────────────────────────────────────────
_sta = network.WLAN(network.STA_IF)
if not _sta.active():
    _sta.active(True)
    utime.sleep_ms(300)
_sta.config(channel=6)

_en = espnow.ESPNow()
try:
    _en.active(True)
except OSError:
    pass   # ya activo desde antes del soft-reboot — OK
try:
    _en.add_peer(BROADCAST_MAC)
except OSError:
    pass   # peer ya registrado — OK

# ───────────────────────────────────────────────
#  COLAS Y ESTADO
# ───────────────────────────────────────────────
cola_subida   = []   # datos malla → Raspberry (CSV)
cola_bajada   = []   # órdenes Raspi → malla (JSON WAVE)
_nodos_vistos = []   # IDs de nodos detectados (últimos 10)
_t_propio     = "--"
_h_propio     = "--"
_wif_ok       = None
_mq_ok        = None
_svr_ok       = None

# ───────────────────────────────────────────────
#  FLAGS IRQ
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

def heartbeat():
    """Parpadeo 80ms — señal de vida. Solo cuando el sistema está estable."""
    tft.fill(NEGRO)
    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    nodos_txt = " ".join(_nodos_vistos[-5:]) if _nodos_vistos else "sin nodos"
    tft.write(font_sm, "PIF v11",    4,   4,  VERDE)
    tft.write(font_sm, hora,         150, 4,  GRIS)
    tft.write(font_sm, nodos_txt[:30], 4, 28, GRIS)
    backlight.value(1)
    utime.sleep_ms(80)
    backlight.value(0)

def pantalla_completa():
    """Pantalla grande T/H + estado. Se queda ON, el caller la apaga."""
    global _wif_ok, _mq_ok, _svr_ok
    ws, wc   = _est(_wif_ok)
    ms, mc   = _est(_mq_ok)
    svs, svc = _est(_svr_ok)
    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    nodos_txt = " ".join(_nodos_vistos[-5:]) if _nodos_vistos else "sin nodos aun"

    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER",               4,   2,  VERDE)
    tft.write(font_sm, hora,                       160, 2,  CYAN)
    tft.write(font_md, "T: {}C".format(_t_propio),  4,  22, AMARILLO)
    tft.write(font_md, "H: {}%".format(_h_propio),  4,  52, CYAN)
    tft.write(font_sm, nodos_txt[:30],              4,  84, GRIS)
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO); tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO); tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO); tft.write(font_sm, svs, 200, 108, svc)
    backlight.value(1)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v11.1",        4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)
    backlight.value(1)
    utime.sleep_ms(2000)
    backlight.value(0)

# ───────────────────────────────────────────────
#  SENSORES
# ───────────────────────────────────────────────
def medir_propio():
    global _t_propio, _h_propio
    for _ in range(3):
        t, h = hw.leer_dht()
        if t != "Error":
            _t_propio = t
            _h_propio = h
            return t, h
        utime.sleep_ms(1000)
    return "Err", "Err"

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
        ts = "T:{}".format(t_val) if t_val is not None else "T:?"
        hs = "H:{}".format(h_val) if h_val is not None else "H:?"
        lineas.append("{},{},{} {},sensor".format(hora, nodo, ts, hs))
    for tipo, val in otros:
        lineas.append("{},{},{},{}".format(hora, nodo, tipo, val))
    return lineas

# ───────────────────────────────────────────────
#  VENTANA MESH
#  _en siempre activo — solo send/recv, sin tocar active()
#  sta.disconnect() garantiza canal libre antes de enviar
# ───────────────────────────────────────────────
def ventana_mesh(cmd="REQ:ALL", target="ALL"):
    global _nodos_vistos

    # Asegurar que no estamos conectados al AP
    # (ESP-NOW funciona mejor sin AP asociado, usa canal 6 configurado)
    if _sta.isconnected():
        try: _sta.disconnect()
        except: pass
        utime.sleep_ms(150)

    # Construir WAVE
    if cola_bajada:
        onda = cola_bajada.pop(0)
    else:
        onda = json.dumps({
            "type"  : "WAVE",
            "cmd"   : cmd,
            "from"  : CLIENT_ID,
            "target": target,
            "ttl"   : 6
        })

    print("[MESH TX]", onda[:60])
    for _ in range(BROADCAST_N):
        try: _en.send(BROADCAST_MAC, onda)
        except Exception as e: print("[TX ERR]", e)
        utime.sleep_ms(150)
    del onda

    # Escuchar FBs
    fin = utime.ticks_add(utime.ticks_ms(), T_MESH_MS)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        try:
            host, msg = _en.recv(10)
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
                if nodo not in _nodos_vistos:
                    _nodos_vistos.append(nodo)
                    if len(_nodos_vistos) > 10:
                        _nodos_vistos.pop(0)
                recibidos += 1
            del data, txt
        except Exception as e:
            print("[RX ERR]", e)

    print("[MESH OK] nodos:", recibidos)
    gc.collect()
    return recibidos

# ───────────────────────────────────────────────
#  VENTANA WIFI+MQTT
#  sta siempre activo — solo connect/disconnect
#  ESP-NOW coexiste: cuando WiFi conectado usa ese canal,
#  slaves en canal 6 pueden no responder durante este tiempo
#  (pero el master igual escucha por si acaso)
# ───────────────────────────────────────────────
def _mqtt_cb(topic, msg):
    txt = msg.decode().strip()
    print("[MQTT RX]", txt)
    tgt = txt[4:] if (txt.startswith("REQ:") and txt != "REQ:ALL") else "ALL"
    cola_bajada.append(json.dumps({
        "type": "WAVE", "cmd": txt,
        "from": CLIENT_ID, "target": tgt, "ttl": 6
    }))

def ventana_wifi():
    global _wif_ok, _mq_ok, _svr_ok
    _wif_ok = _mq_ok = _svr_ok = False

    if not _sta.isconnected():
        _sta.connect(WIFI_SSID, WIFI_PASS)
        fin = utime.ticks_add(utime.ticks_ms(), T_WIFI_MS)
        while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
            if _sta.isconnected(): break
            utime.sleep_ms(300)

    if not _sta.isconnected():
        print("[WIFI] Sin conexion")
        return

    _wif_ok = True
    print("[WIFI] Conectado:", _sta.ifconfig()[0])

    client = None
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(_mqtt_cb)
        client.connect()
        _mq_ok = True

        client.subscribe(TOPIC_SUB)
        client.check_msg()

        enviados = 0
        while cola_subida:
            item = cola_subida.pop(0)
            client.publish(TOPIC_PUB, item)
            enviados += 1
            print("[MQTT TX]", item)

        _svr_ok = True
        print("[MQTT OK] enviados:", enviados)

    except Exception as e:
        print("[MQTT ERR]", e)
    finally:
        if client:
            try: client.disconnect()
            except: pass
            del client
        gc.collect()

    # Soltar AP → ESP-NOW vuelve a canal 6
    try: _sta.disconnect()
    except: pass
    utime.sleep_ms(200)

# ───────────────────────────────────────────────
#  LOOP PRINCIPAL
# ───────────────────────────────────────────────
def loop():
    global _flag_server, _flag_mesh

    t_ultimo_10min  = utime.ticks_ms() - T_10MIN_MS   # forzar display al inicio
    t_ultimo_hb     = utime.ticks_ms()

    while True:
        gc.collect()
        ahora = utime.ticks_ms()

        # ── Heartbeat cada 5s (solo cuando stable) ─
        if utime.ticks_diff(ahora, t_ultimo_hb) >= T_HEARTBEAT:
            heartbeat()
            t_ultimo_hb = utime.ticks_ms()

        # ── Botón servidor: mide + sube ahora ──────
        if _flag_server:
            _flag_server = False
            print("[BTN] Forzar servidor")
            t, h = medir_propio()
            hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
            if t != "Err":
                cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))
            ventana_wifi()
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_S * 1000)
            backlight.value(0)
            t_ultimo_10min = utime.ticks_ms()
            t_ultimo_hb    = utime.ticks_ms()
            continue

        # ── Botón mesh: WAVE a todos ahora ─────────
        if _flag_mesh:
            _flag_mesh = False
            print("[BTN] Forzar mesh")
            ventana_mesh(cmd="REQ:ALL", target="ALL")
            t_ultimo_hb = utime.ticks_ms()
            continue

        # ── Ciclo normal: mesh → wifi ───────────────
        ventana_mesh()
        t_ultimo_hb = utime.ticks_ms()

        ventana_wifi()
        t_ultimo_hb = utime.ticks_ms()

        # ── Pantalla cada 10 min ────────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_10min) >= T_10MIN_MS:
            t, h = medir_propio()
            hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
            if t != "Err":
                cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_S * 1000)
            backlight.value(0)
            t_ultimo_10min = utime.ticks_ms()
            t_ultimo_hb    = utime.ticks_ms()

        print("[RAM]", gc.mem_free(), "| sub:", len(cola_subida), "| nodos:", len(_nodos_vistos))

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
gc.collect()

while True:
    try:
        loop()
    except Exception as e:
        print("[FATAL]", e)
        tft.fill(NEGRO)
        tft.write(font_md, "ERROR",     4, 8,  ROJO)
        tft.write(font_sm, str(e)[:26], 4, 50, BLANCO)
        backlight.value(1)
        utime.sleep_ms(5000)
        backlight.value(0)
        gc.collect()
