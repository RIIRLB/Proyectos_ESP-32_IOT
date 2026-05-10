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
from machine import Pin, I2C
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
T_MESH_LISTEN  = 8_000
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
_ax_propio    = "--"
_ay_propio    = "--"
_az_propio    = "--"
_estado_mov   = "---"   # ESTABLE / MOVIDO / AGITADO
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

# IMPORTANTE: crear I2C(0) AQUÍ con freq=50000 (igual que sens.py).
# Sensores intentará crear I2C(0) con freq=50000 también; al ser idéntica
# configuración, no hay conflicto. Si pasamos otra freq, abort() en core 1.
MPU_ADDR = 0x68
i2c      = I2C(0, sda=Pin(21), scl=Pin(22), freq=50000)
_mpu_ok  = False

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
# ───────────────────────────────────────────────
#  MPU9250/6500
# ───────────────────────────────────────────────
def _s16(v):
    return v if v < 32768 else v - 65536

def mpu_init():
    """Despierta el MPU del modo sleep. Llamar una vez al arrancar."""
    global _mpu_ok
    try:
        whoami = i2c.readfrom_mem(MPU_ADDR, 0x75, 1)[0]
        if whoami in (0x68, 0x70, 0x71, 0x73):
            i2c.writeto_mem(MPU_ADDR, 0x6B, b'\x00')   # PWR_MGMT_1 = 0 (wake)
            utime.sleep_ms(80)
            _mpu_ok = True
            print("[MPU] OK whoami=0x{:02X}".format(whoami))
            return True
    except Exception as e:
        print("[MPU] init fallo:", e)
    _mpu_ok = False
    return False

def mpu_leer():
    """Devuelve (ax, ay, az) en g, o (None, None, None) si falla."""
    try:
        raw = i2c.readfrom_mem(MPU_ADDR, 0x3B, 6)
        ax  = round(_s16(raw[0] << 8 | raw[1]) / 16384.0, 2)
        ay  = round(_s16(raw[2] << 8 | raw[3]) / 16384.0, 2)
        az  = round(_s16(raw[4] << 8 | raw[5]) / 16384.0, 2)
        return ax, ay, az
    except:
        return None, None, None

def medir_propio():
    """Lee MPU y calcula estado de movimiento."""
    global _ax_propio, _ay_propio, _az_propio, _estado_mov
    if not _mpu_ok:
        # Reintentar init por si el sensor se reconectó
        if not mpu_init():
            _ax_propio = _ay_propio = _az_propio = "ERR"
            _estado_mov = "ERR"
            return "ERR", "ERR", "ERR"
    ax, ay, az = mpu_leer()
    if ax is None:
        _ax_propio = _ay_propio = _az_propio = "ERR"
        _estado_mov = "ERR"
        return "ERR", "ERR", "ERR"
    _ax_propio = str(ax)
    _ay_propio = str(ay)
    _az_propio = str(az)
    # Estado por magnitud aproximada (en reposo da ~1g)
    try:
        mag = (ax*ax + ay*ay + az*az) ** 0.5
        if   mag < 1.15: _estado_mov = "ESTABLE"
        elif mag < 1.5:  _estado_mov = "MOVIDO"
        else:            _estado_mov = "AGITADO"
    except:
        _estado_mov = "---"
    return _ax_propio, _ay_propio, _az_propio

def encolar_propio(ts):
    """Agrega las 3 líneas CSV de aceleración del Master a cola_subida."""
    cola_subida.append("{},{},AccX,{}".format(ts, CLIENT_ID, _ax_propio))
    cola_subida.append("{},{},AccY,{}".format(ts, CLIENT_ID, _ay_propio))
    cola_subida.append("{},{},AccZ,{}".format(ts, CLIENT_ID, _az_propio))

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
    tft.write(font_sm, "PIF MASTER v16.4", 4, 4,  VERDE)
    tft.write(font_sm, linea1[:28],       4, 28, col1)
    if linea2:
        tft.write(font_sm, linea2[:28],   4, 52, col2)
    backlight.value(1)

def heartbeat():
    h     = hora_local()
    nodos = " ".join(_nodos_vistos[-4:]) if _nodos_vistos else "sin nodos"
    tft.fill(NEGRO)
    tft.write(font_sm, "Master v16.4",  4,   4,  VERDE)
    tft.write(font_sm, h,             148, 4,  GRIS)
    tft.write(font_sm, "X:{} Y:{} Z:{}".format(_ax_propio, _ay_propio, _az_propio), 4, 26, AMARILLO)
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
    # Estado de movimiento con color según valor
    if   _estado_mov == "ESTABLE": e_col = VERDE
    elif _estado_mov == "MOVIDO":  e_col = AMARILLO
    elif _estado_mov == "AGITADO": e_col = ROJO
    else:                          e_col = GRIS
    tft.write(font_sm, "MOVIMIENTO", 4, 22, GRIS)
    tft.write(font_md, _estado_mov, 4, 38, e_col)
    tft.write(font_sm, "X:{} Y:{}".format(_ax_propio, _ay_propio), 4, 72, BLANCO)
    tft.write(font_sm, "Z:{}  {}".format(_az_propio, nodos[:14]), 4, 90, GRIS)
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO); tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO); tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO); tft.write(font_sm, svs, 200, 108, sc)
    backlight.value(1)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v16.4",        4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)
    backlight.value(1)
    utime.sleep_ms(2000)
    backlight.value(0)

# ───────────────────────────────────────────────
#  VENTANA WIFI
#  WiFi ya está activo — solo conectar/desconectar del AP.
#  No tocar sta.active() aquí.
# ───────────────────────────────────────────────
_msg_counter = 0   # contador para msg_id únicos

def _next_msg_id():
    global _msg_counter
    _msg_counter += 1
    return _msg_counter

def _mqtt_cb(topic, msg):
    txt = msg.decode().strip()
    print("\n[<<< MQTT RX]", txt, "topic:", topic)
    if txt == "PAIR":
        ts = hora_local(con_fecha=True)
        encolar_propio(ts)
        print("[PAIR] Respondiendo")
        return
    tgt = txt[4:] if (txt.startswith("REQ:") and txt != "REQ:ALL") else "ALL"
    mid = _next_msg_id()
    paquete = json.dumps({
        "type"  : "WAVE",
        "cmd"   : txt,
        "from"  : CLIENT_ID,
        "target": tgt,
        "ttl"   : 6,
        "ch"    : _canal_actual,
        "mid"   : mid
    })
    cola_bajada.append(paquete)
    print("[CMD ENCOLADO] mid:{}  cmd:{}  target:{}  cola_bajada:{}".format(
        mid, txt, tgt, len(cola_bajada)))

def ventana_wifi(modo="completo"):
    """
    modo='completo' → conecta + escucha comandos del servidor (1.2s) + publica datos
    modo='flush'    → conecta + publica datos + sale (sin polling de comandos)
                      Para envío rápido tras recibir FBs de slaves.
    """
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
        print("[WIFI] Sin conexion (modo={})".format(modo))
        return

    _wif_ok = True
    try:
        _canal_actual = sta.config('channel')
    except:
        pass
    print("[WIFI] OK ch:{} ip:{} modo:{}".format(
        _canal_actual, sta.ifconfig()[0], modo))

    # NTP solo en modo completo (en flush no perdemos tiempo)
    if not _ntp_ok and modo == "completo":
        try:
            ntptime.host = "pool.ntp.org"
            ntptime.settime()
            _ntp_ok = True
            print("[NTP] Sincronizado")
        except Exception as e:
            print("[NTP] Fallo:", e)

    client = None
    for intento in range(2):
        try:
            client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
            client.set_callback(_mqtt_cb)
            client.connect()
            _mq_ok = True

            # Polling de comandos solo en modo completo
            if modo == "completo":
                client.subscribe(TOPIC_SUB)
                print("[MQTT] Polling comandos servidor...")
                for poll_i in range(4):     # 4 polls × 150ms = 600ms
                    client.check_msg()
                    utime.sleep_ms(150)

            enviados = 0
            while cola_subida:
                item = cola_subida.pop(0)
                client.publish(TOPIC_PUB, item)
                enviados += 1
                print("[MQTT TX]", item)

            _svr_ok = True
            print("[MQTT OK] enviados:", enviados, "modo:", modo)
            break

        except Exception as e:
            print("[MQTT ERR] intento {}: {}".format(intento + 1, e))
            if client:
                try: client.disconnect()
                except: pass
                del client
                client = None
            gc.collect()
            if intento == 0:
                # Pausa más corta en flush para no retrasar tanto
                utime.sleep_ms(500 if modo == "flush" else 1500)
    else:
        pass

    if client:
        try: client.disconnect()
        except: pass
        del client
    gc.collect()

    try: sta.disconnect()
    except: pass
    utime.sleep_ms(200)

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

    # Fijar canal
    try:
        sta = network.WLAN(network.STA_IF)
        sta.config(channel=_canal_actual)
    except: pass
    utime.sleep_ms(150)

    # Construir WAVE
    cmd_mostrar = cmd
    mid_actual  = None
    if cola_bajada:
        onda = cola_bajada.pop(0)
        try:
            d = json.loads(onda)
            cmd_mostrar = d.get("cmd", "WAVE")
            mid_actual  = d.get("mid")
        except: pass
        print("\n[>>> MESH TX] mid:{}  cmd:{}  bytes:{}".format(
            mid_actual, cmd_mostrar, len(onda)))
        ui_status("WAVE servidor", cmd_mostrar[:20], CYAN, AMARILLO)
    else:
        mid_actual = _next_msg_id()
        onda = json.dumps({
            "type"  : "WAVE",
            "cmd"   : cmd,
            "from"  : CLIENT_ID,
            "target": target,
            "ttl"   : 6,
            "ch"    : _canal_actual,
            "mid"   : mid_actual
        })
        print("\n[>>> MESH TX auto] mid:{}  cmd:{}".format(mid_actual, cmd))

    # ACK de hardware ESP-NOW: en.send() devuelve True si llegó al menos a un peer.
    # En broadcast siempre devuelve True (no hay ACK real), pero registramos.
    exitos = 0
    fallos = 0
    for i in range(BROADCAST_N):
        try:
            ok = _en.send(BROADCAST_MAC, onda)
            if ok: exitos += 1
            else:  fallos += 1
        except Exception as e:
            fallos += 1
            print("[TX ERR]", e)
        utime.sleep_ms(150)
    print("[TX DONE] mid:{}  exitos:{}/{}  fallos:{}".format(
        mid_actual, exitos, BROADCAST_N, fallos))
    del onda

    # Escuchar FBs
    fin = utime.ticks_add(utime.ticks_ms(), T_MESH_LISTEN)
    recibidos = 0

    # Dedup: cada nodo solo se procesa una vez por ventana mesh,
    # ignorando los duplicados de envíos repetidos del slave (3-5x) y del relay
    nodos_procesados_esta_ventana = set()

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
                mid_fb  = data.get("mid", "?")
                if nodo in nodos_procesados_esta_ventana:
                    print("[DEDUP] FB duplicado de", nodo, "ignorado")
                    del data, txt
                    continue
                nodos_procesados_esta_ventana.add(nodo)
                payload = data.get("payload") or data.get("pl") or []
                ts      = hora_local(con_fecha=True)
                for linea in _payload_csv(ts, nodo, payload):
                    cola_subida.append(linea)
                if nodo not in _nodos_vistos:
                    _nodos_vistos.append(nodo)
                    if len(_nodos_vistos) > 10:
                        _nodos_vistos.pop(0)
                recibidos += 1
                print("[<<< FB RX] nodo:{}  mid:{}  items:{}".format(
                    nodo, mid_fb, len(payload)))
            del data, txt
        except Exception as e:
            print("[RX ERR]", e)

    print("[MESH OK] recibidos:", recibidos, "| nodos únicos:", len(nodos_procesados_esta_ventana))
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
            ui_status("Servidor", "publicando...", AMARILLO, GRIS)
            ts = hora_local(con_fecha=True)
            encolar_propio(ts)
            ventana_wifi(modo="flush")
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
            # Publicar inmediatamente lo recibido al servidor
            if cola_subida:
                ui_status("Subiendo", "{} datos".format(len(cola_subida)), CYAN, GRIS)
                ventana_wifi(modo="flush")
                reset_radio()
            t_ultimo_hb = utime.ticks_ms()
            continue

        # ── Medir sensores ─────────────────────────────────────────────
        medir_propio()

        # ── Ciclo normal ───────────────────────────────────────────────
        ventana_wifi()
        reset_radio()

        # Si llegaron comandos del servidor en este ciclo WiFi,
        # mostrar pantalla y procesarlos inmediatamente
        if cola_bajada:
            backlight.value(1)
            ui_status("Comando recibido", "ejecutando...", CYAN, AMARILLO)

        ventana_mesh()
        reset_radio()

        # Si recibimos FBs de slaves en mesh, publicarlos AHORA
        # sin esperar al siguiente ciclo (que tarda ~10s)
        if cola_subida:
            print("[FLUSH] Publicando", len(cola_subida), "datos al servidor")
            ventana_wifi(modo="flush")   # rápido, sin polling de comandos
            reset_radio()

        # Si procesamos comando del servidor, mantener pantalla encendida
        if backlight.value() == 1:
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_MS)
            backlight.value(0)

        t_ultimo_hb = utime.ticks_ms()

        # ── Pantalla cada 10 min ───────────────────────────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_10min) >= T_10MIN:
            ts = hora_local(con_fecha=True)
            encolar_propio(ts)
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_MS)
            backlight.value(0)
            t_ultimo_10min = t_ultimo_hb = utime.ticks_ms()

        # ── Heartbeat ──────────────────────────────────────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_hb) >= T_HEARTBEAT:
            heartbeat()
            t_ultimo_hb = utime.ticks_ms()

        print("[RAM]", gc.mem_free(),
              "| X:{} Y:{} Z:{} {}".format(_ax_propio, _ay_propio, _az_propio, _estado_mov),
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

# Inicializar MPU. Si falla, el sistema sigue (solo no medirá movimiento).
if not mpu_init():
    print("[MPU] No detectado — verifica cableado SDA=21 SCL=22")
    tft.fill(NEGRO)
    tft.write(font_md, "MPU NO DETECTADO", 4,  8,  AMARILLO)
    tft.write(font_sm, "Revisa cables",    4,  44, BLANCO)
    tft.write(font_sm, "SDA=21 SCL=22",    4,  68, BLANCO)
    tft.write(font_sm, "Sigue sin MPU...",  4, 92, GRIS)
    backlight.value(1)
    utime.sleep_ms(3000)
    backlight.value(0)

print("[OK] Master v16.4 listo")

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
