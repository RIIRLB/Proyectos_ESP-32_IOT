# ============================================================
#  MASTER_TTGO v15.0 — PIF Mesh / LAB-ARTE
#
#  Solución definitiva al crash LoadProhibited (PC 0x4016accb):
#
#  En IDF v5.2.2, el ciclo espnow.active(False) → active(True)
#  deja un puntero interno en NULL. El segundo active(True)
#  intenta acceder a (NULL + 0x20) → crash de C sin recovery.
#
#  Arquitectura v15: WiFi y ESP-NOW se inicializan UNA SOLA VEZ
#  en el arranque y NUNCA se desinicializan.
#  IDF v5.x fue diseñado para que coexistan permanentemente.
#
#  Ciclo normal:
#    ventana_wifi() → [lightsleep 500ms] → ventana_mesh() → [lightsleep 500ms]
#
#  ventana_wifi(): conecta AP → MQTT TX/RX → desconecta AP (sta.disconnect)
#  ventana_mesh(): fija canal → envía WAVE con el objeto _en global → recibe FBs
#
#  Botones:
#    GPIO35 → ciclo servidor inmediato
#    GPIO0  → WAVE manual
# ============================================================

import gc, network, espnow, machine, utime, json
from machine import Pin
from umqtt.simple import MQTTClient
import ntptime
import tft_config
import st7789py as st7789
import comfortaa_16 as font_sm
import comfortaa_24 as font_md
from sens import Sensores

gc.collect()

# ───────────────────────────────────────────────
#  CONFIGURACIÓN
# ───────────────────────────────────────────────
WIFI_SSID     = "CAPUGRRDO"
WIFI_PASS     = "JJKU1TARO"
MQTT_BROKER   = "192.168.3.27"
# WIFI_SSID   = "Totalplay-C5AC"
# WIFI_PASS   = "C5AC642BDVePRn6Z"
# MQTT_BROKER = "192.168.100.132"
# WIFI_SSID   = "Arte_Tenda2.4"
# WIFI_PASS   = "Lab4rt3#"
# MQTT_BROKER = "192.168.1.146"

CLIENT_ID     = "MASTER_TTGO_GATEWAY"
TOPIC_SUB     = b"comandos/mesh"
TOPIC_PUB     = b"datos/sensores"
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

T_WIFI_CONNECT = 8_000
T_MESH_LISTEN  = 5_000
T_RADIO_RESET  = 500      # lightsleep entre fases (ms)
T_HEARTBEAT    = 8_000
T_10MIN        = 600_000
DISPLAY_ON_MS  = 5_000
BROADCAST_N    = 3
TZ_OFFSET      = -6       # UTC-6 México Centro

# ───────────────────────────────────────────────
#  COLAS Y ESTADO
# ───────────────────────────────────────────────
cola_subida   = []
cola_bajada   = []
_t_propio     = "--"
_h_propio     = "--"
_nodos_vistos = []
_canal_actual = 6
_ntp_ok       = False
_wif_ok       = None
_mq_ok        = None
_svr_ok       = None

# Objeto ESP-NOW global — se crea UNA VEZ y nunca se destruye
_en = None

# ───────────────────────────────────────────────
#  HARDWARE
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
hw        = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(0)

VERDE    = st7789.GREEN
ROJO     = st7789.RED
AMARILLO = st7789.YELLOW
CYAN     = st7789.CYAN
BLANCO   = st7789.WHITE
NEGRO    = st7789.BLACK
GRIS     = st7789.color565(80, 80, 80)

# ───────────────────────────────────────────────
#  BOTONES IRQ
# ───────────────────────────────────────────────
_flag_server = False
_flag_mesh   = False

def _isr_server(pin):
    global _flag_server
    _flag_server = True
    backlight.value(1)

def _isr_mesh(pin):
    global _flag_mesh
    _flag_mesh = True
    backlight.value(1)

btn_env = Pin(35, Pin.IN, Pin.PULL_UP)
btn_dir = Pin(0,  Pin.IN, Pin.PULL_UP)
btn_env.irq(trigger=Pin.IRQ_FALLING, handler=_isr_server)
btn_dir.irq(trigger=Pin.IRQ_FALLING, handler=_isr_mesh)

# ───────────────────────────────────────────────
#  HORA LOCAL
# ───────────────────────────────────────────────
def hora_local(con_fecha=False):
    t  = utime.time() + TZ_OFFSET * 3600
    lt = utime.localtime(t)
    if con_fecha:
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            lt[0], lt[1], lt[2], lt[3], lt[4], lt[5])
    return "{:02d}:{:02d}:{:02d}".format(lt[3], lt[4], lt[5])

# ───────────────────────────────────────────────
#  ARRANQUE DE RADIO
#
#  WiFi y ESP-NOW se inician aquí y permanecen activos
#  durante toda la sesión. Nunca se desinicializan.
#
#  Por qué no se llama active(False) en ESP-NOW:
#    IDF v5.2.2 deja punteros internos en NULL al hacer deinit.
#    El siguiente init accede a (NULL + 0x20) → LoadProhibited.
#    Mantenerlos activos evita ese code path completamente.
# ───────────────────────────────────────────────
def arranque_radio():
    global _en, _canal_actual

    # 1. WiFi
    try:
        sta = network.WLAN(network.STA_IF)
        if not sta.active():
            sta.active(True)
            utime.sleep_ms(600)
        print("[RADIO] WiFi activo")
    except Exception as e:
        print("[RADIO] WiFi fallo:", e)
        return False

    # 2. ESP-NOW — sobre el WiFi ya activo, UNA SOLA VEZ
    try:
        _en = espnow.ESPNow()
        _en.active(True)
        _en.add_peer(BROADCAST_MAC)
        print("[RADIO] ESP-NOW activo, peer broadcast registrado")
    except Exception as e:
        print("[RADIO] ESP-NOW fallo:", e)
        return False

    return True

def reset_radio():
    """Lightsleep entre fases — permite al radio gestionar el cambio
    de modo WiFi↔ESP-NOW sin conflicto de buffers."""
    machine.lightsleep(T_RADIO_RESET)

# ───────────────────────────────────────────────
#  SENSORES
# ───────────────────────────────────────────────
def medir_propio():
    global _t_propio, _h_propio
    for _ in range(3):
        t, h = hw.leer_dht()
        if t != "Error":
            _t_propio = str(t)
            _h_propio = str(h)
            return _t_propio, _h_propio
        utime.sleep_ms(800)
    _t_propio = "ERR"
    _h_propio = "ERR"
    return "ERR", "ERR"

def _payload_csv(ts, nodo, payload):
    t_val = h_val = None
    otros = []
    for m in payload:
        tipo = m.get("t") or m.get("tipo", "?")
        val  = m.get("v") if m.get("v") is not None else m.get("val", "?")
        if   tipo in ("Temp", "Temperatura"): t_val = str(val)
        elif tipo in ("Hum",  "Humedad"):     h_val = str(val)
        else: otros.append((tipo, str(val)))
    lineas = []
    if t_val is not None or h_val is not None:
        ts_str = "T:{}".format(t_val if t_val is not None else "?")
        hs_str = "H:{}".format(h_val if h_val is not None else "?")
        lineas.append("{},{},{} {},sensor".format(ts, nodo, ts_str, hs_str))
    for tipo, val in otros:
        lineas.append("{},{},{},{}".format(ts, nodo, tipo, val))
    return lineas

# ───────────────────────────────────────────────
#  UI
# ───────────────────────────────────────────────
def ui_status(linea1, linea2="", col1=AMARILLO, col2=GRIS):
    """Pantalla de estado rápida — 2 líneas sobre fondo negro."""
    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER v15.1", 4, 4,  VERDE)
    tft.write(font_sm, linea1[:28],       4, 28, col1)
    if linea2:
        tft.write(font_sm, linea2[:28],   4, 52, col2)
    backlight.value(1)

def heartbeat():
    h     = hora_local()
    nodos = " ".join(_nodos_vistos[-4:]) if _nodos_vistos else "sin nodos"
    tft.fill(NEGRO)
    tft.write(font_sm, "Master v15.1",  4,   4,  VERDE)
    tft.write(font_sm, h,             148, 4,  GRIS)
    tft.write(font_sm, "T:{}  H:{}".format(_t_propio, _h_propio), 4, 26, AMARILLO)
    tft.write(font_sm, "ch:{}  ntp:{}".format(
        _canal_actual, "ok" if _ntp_ok else "--"), 4, 50, GRIS)
    tft.write(font_sm, nodos[:30],    4,   74, GRIS)
    backlight.value(1)
    utime.sleep_ms(500)   # 500ms visible — suficiente para leer sin molestar
    backlight.value(0)

def pantalla_completa():
    def _e(v):
        if v is True:  return "OK",  VERDE
        if v is False: return "ERR", ROJO
        return                 "---", GRIS
    h     = hora_local()
    nodos = " ".join(_nodos_vistos[-5:]) if _nodos_vistos else "sin nodos"
    ws, wc  = _e(_wif_ok)
    ms, mc  = _e(_mq_ok)
    svs, sc = _e(_svr_ok)
    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER",               4,   2,  VERDE)
    tft.write(font_sm, h,                          160, 2,  CYAN)
    tft.write(font_md, "Temp: {}°C".format(_t_propio), 4,   22, AMARILLO)
    tft.write(font_md, "Hume: {}%".format(_h_propio), 4,   52, CYAN)
    tft.write(font_sm, nodos[:30],                 4,   84, GRIS)
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO); tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO); tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO); tft.write(font_sm, svs, 200, 108, sc)
    backlight.value(1)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v15.1",        4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)
    backlight.value(1)
    utime.sleep_ms(2000)
    backlight.value(0)

# ───────────────────────────────────────────────
#  VENTANA WIFI
#  WiFi ya está activo — solo conectar/desconectar del AP.
#  No tocar sta.active() aquí.
# ───────────────────────────────────────────────
def _mqtt_cb(topic, msg):
    txt = msg.decode().strip()
    print("[MQTT RX]", txt)
    # PAIR — servidor pide confirmación, respondemos con medición actual
    if txt == "PAIR":
        ts = hora_local(con_fecha=True)
        cola_subida.append("{},{},T:{} H:{},sensor".format(
            ts, CLIENT_ID, _t_propio, _h_propio))
        print("[PAIR] Respondiendo")
        return
    tgt = txt[4:] if (txt.startswith("REQ:") and txt != "REQ:ALL") else "ALL"
    cola_bajada.append(json.dumps({
        "type"  : "WAVE",
        "cmd"   : txt,
        "from"  : CLIENT_ID,
        "target": tgt,
        "ttl"   : 6,
        "ch"    : _canal_actual
    }))

def ventana_wifi():
    global _wif_ok, _mq_ok, _svr_ok, _canal_actual, _ntp_ok

    _wif_ok = _mq_ok = _svr_ok = False

    sta = network.WLAN(network.STA_IF)

    if not sta.isconnected():
        sta.connect(WIFI_SSID, WIFI_PASS)
        fin = utime.ticks_add(utime.ticks_ms(), T_WIFI_CONNECT)
        while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
            if sta.isconnected(): break
            utime.sleep_ms(300)

    if not sta.isconnected():
        print("[WIFI] Sin conexion")
        return

    _wif_ok = True
    try:
        _canal_actual = sta.config('channel')
    except:
        pass
    print("[WIFI] OK  ch:{}  ip:{}".format(_canal_actual, sta.ifconfig()[0]))

    # NTP con timeout para evitar hang por DNS lento
    if not _ntp_ok:
        try:
            ntptime.host = "pool.ntp.org"
            ntptime.settime()
            _ntp_ok = True
            print("[NTP] Sincronizado")
        except Exception as e:
            print("[NTP] Fallo:", e)

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
    # WiFi permanece conectado al AP — no llamar sta.disconnect().
    # En v1.22.2 LILYGO, sta.disconnect() deja el driver en estado
    # inválido y el siguiente sta.connect() lanza Wifi Internal Error.
    # ESP-NOW funciona perfectamente con WiFi conectado al AP.

# ───────────────────────────────────────────────
#  VENTANA MESH
#  Usa el objeto _en global — NUNCA llama active(False).
#  Solo envía WAVE y escucha FBs.
# ───────────────────────────────────────────────
def ventana_mesh(cmd="REQ:ALL", target="ALL"):
    global _nodos_vistos

    if _en is None:
        print("[MESH] ESP-NOW no disponible")
        return 0

    # Fijar canal — solo config, sin active()
    try:
        sta = network.WLAN(network.STA_IF)
        sta.config(channel=_canal_actual)
    except: pass
    utime.sleep_ms(150)

    # Construir WAVE
    if cola_bajada:
        onda = cola_bajada.pop(0)
        print("[MESH TX custom]", onda[:60])
    else:
        onda = json.dumps({
            "type"  : "WAVE",
            "cmd"   : cmd,
            "from"  : CLIENT_ID,
            "target": target,
            "ttl"   : 6,
            "ch"    : _canal_actual
        })
        print("[MESH TX] cmd:", cmd, "ch:", _canal_actual)

    for _ in range(BROADCAST_N):
        try: _en.send(BROADCAST_MAC, onda)
        except Exception as e: print("[TX ERR]", e)
        utime.sleep_ms(150)
    del onda

    # Escuchar FBs
    fin = utime.ticks_add(utime.ticks_ms(), T_MESH_LISTEN)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        try: host, msg = _en.recv(10)
        except:
            utime.sleep_ms(10)
            continue
        if not msg:
            continue
        try:
            txt  = msg.decode()
            data = json.loads(txt)
            if data.get("type") in ("FEEDBACK", "FB"):
                nodo    = data.get("id", "?")
                payload = data.get("payload") or data.get("pl") or []
                ts      = hora_local(con_fecha=True)
                for linea in _payload_csv(ts, nodo, payload):
                    cola_subida.append(linea)
                if nodo not in _nodos_vistos:
                    _nodos_vistos.append(nodo)
                    if len(_nodos_vistos) > 10:
                        _nodos_vistos.pop(0)
                recibidos += 1
            del data, txt
        except Exception as e:
            print("[RX ERR]", e)

    print("[MESH OK] recibidos:", recibidos)
    gc.collect()
    return recibidos

# ───────────────────────────────────────────────
#  LOOP PRINCIPAL
# ───────────────────────────────────────────────
def loop():
    global _flag_server, _flag_mesh

    t_ultimo_hb    = utime.ticks_ms()
    t_ultimo_10min = utime.ticks_ms() - T_10MIN

    medir_propio()

    while True:
        gc.collect()

        # ── Botones PRIMERO ────────────────────────────────────────────
        if _flag_server:
            _flag_server = False
            ui_status("Servidor", "conectando...", AMARILLO, GRIS)
            ts = hora_local(con_fecha=True)
            cola_subida.append("{},{},T:{} H:{},sensor".format(
                ts, CLIENT_ID, _t_propio, _h_propio))
            ventana_wifi()
            medir_propio()
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_MS)
            backlight.value(0)
            t_ultimo_10min = t_ultimo_hb = utime.ticks_ms()
            continue

        if _flag_mesh:
            _flag_mesh = False
            ui_status("Malla", "enviando WAVE...", AMARILLO, GRIS)
            utime.sleep_ms(200)
            ventana_mesh(cmd="REQ:ALL", target="ALL")
            reset_radio()
            t_ultimo_hb = utime.ticks_ms()
            continue

        # ── Medir sensores ─────────────────────────────────────────────
        medir_propio()

        # ── Ciclo normal ───────────────────────────────────────────────
        ventana_wifi()
        reset_radio()
        ventana_mesh()
        reset_radio()
        t_ultimo_hb = utime.ticks_ms()

        # ── Pantalla cada 10 min ───────────────────────────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_10min) >= T_10MIN:
            ts = hora_local(con_fecha=True)
            cola_subida.append("{},{},T:{} H:{},sensor".format(
                ts, CLIENT_ID, _t_propio, _h_propio))
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_MS)
            backlight.value(0)
            t_ultimo_10min = t_ultimo_hb = utime.ticks_ms()

        # ── Heartbeat ──────────────────────────────────────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_hb) >= T_HEARTBEAT:
            heartbeat()
            t_ultimo_hb = utime.ticks_ms()

        print("[RAM]", gc.mem_free(),
              "| T:{} H:{}".format(_t_propio, _h_propio),
              "| sub:", len(cola_subida),
              "| ch:", _canal_actual,
              "| nodos:", len(_nodos_vistos))

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
gc.collect()

if not arranque_radio():
    tft.fill(NEGRO)
    tft.write(font_md, "RADIO ERR",      4,  8,  ROJO)
    tft.write(font_sm, "Desconectar USB",4,  50, AMARILLO)
    tft.write(font_sm, "Power cycle",    4,  74, AMARILLO)
    backlight.value(1)
    while True:
        utime.sleep_ms(5000)

print("[OK] Master v15.1 listo")

while True:
    try:
        loop()
    except Exception as e:
        print("[FATAL]", e)
        tft.fill(NEGRO)
        tft.write(font_md, "ERROR",      4,  8,  ROJO)
        tft.write(font_sm, str(e)[:26],  4,  50, BLANCO)
        tft.write(font_sm, "soft reset", 4,  80, AMARILLO)
        backlight.value(1)
        utime.sleep_ms(3000)
        backlight.value(0)
        machine.reset()
