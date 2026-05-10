# ============================================================
#  MASTER_TTGO v17.0 — PIF Mesh / LAB-ARTE
#
#  Cambios vs v16.x:
#  - Comandos del servidor por HTTP GET (no MQTT subscribe)
#  - MQTT solo para PUBLICAR datos al servidor
#  - Push prioritario: tras mesh, check HTTP rápido y mesh extra si hay cmd
#  - Latencia botón→nodo: ~2-3s en lugar de ~5-10s
# ============================================================

import gc, network, espnow, machine, utime, json
from machine import Pin
from umqtt.simple import MQTTClient
import urequests
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
SERVER_IP     = "192.168.3.27"
# WIFI_SSID   = "Totalplay-C5AC"
# WIFI_PASS   = "C5AC642BDVePRn6Z"
# MQTT_BROKER = "192.168.100.132"
# WIFI_SSID   = "Arte_Tenda2.4"
# WIFI_PASS   = "Lab4rt3#"
# MQTT_BROKER = "192.168.1.146"

SERVER_PORT   = 5000
MQTT_BROKER   = SERVER_IP

CLIENT_ID     = "MASTER_TTGO_GATEWAY"
TOPIC_PUB     = b"datos/sensores"
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

URL_COMANDOS  = "http://{}:{}/comandos".format(SERVER_IP, SERVER_PORT)
URL_ACK       = "http://{}:{}/comandos/ack".format(SERVER_IP, SERVER_PORT)

T_WIFI_CONNECT = 8_000
T_MESH_LISTEN  = 8_000
T_RADIO_RESET  = 500
T_HEARTBEAT    = 8_000
T_10MIN        = 600_000
DISPLAY_ON_MS  = 5_000
BROADCAST_N    = 3
TZ_OFFSET      = -6

# ───────────────────────────────────────────────
#  ESTADO
# ───────────────────────────────────────────────
cola_subida   = []
cola_bajada   = []
_t_propio     = "--"
_h_propio     = "--"
_nodos_vistos = {}   # {ID: {ultimo_visto, parent, count, via_directo, via_relay}}
_canal_actual = 6
_ntp_ok       = False
_wif_ok       = None
_mq_ok        = None
_svr_ok       = None
_en           = None
_msg_counter  = 0

def _next_msg_id():
    global _msg_counter
    _msg_counter += 1
    return _msg_counter

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
#  HORA
# ───────────────────────────────────────────────
def hora_local(con_fecha=False):
    t  = utime.time() + TZ_OFFSET * 3600
    lt = utime.localtime(t)
    if con_fecha:
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            lt[0], lt[1], lt[2], lt[3], lt[4], lt[5])
    return "{:02d}:{:02d}:{:02d}".format(lt[3], lt[4], lt[5])

# ───────────────────────────────────────────────
#  RADIO
# ───────────────────────────────────────────────
def arranque_radio():
    global _en
    try:
        sta = network.WLAN(network.STA_IF)
        if not sta.active():
            sta.active(True)
            utime.sleep_ms(600)
        print("[RADIO] WiFi activo")
    except Exception as e:
        print("[RADIO] WiFi fallo:", e)
        return False
    try:
        _en = espnow.ESPNow()
        _en.active(True)
        _en.add_peer(BROADCAST_MAC)
        print("[RADIO] ESP-NOW activo")
    except Exception as e:
        print("[RADIO] ESP-NOW fallo:", e)
        return False
    return True

def reset_radio():
    machine.lightsleep(T_RADIO_RESET)

# ───────────────────────────────────────────────
#  SENSOR PROPIO
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

def encolar_status(ts):
    if not _nodos_vistos:
        cola_subida.append("{},{},STATUS,(sin nodos)".format(ts, CLIENT_ID))
        return
    ahora  = utime.ticks_ms()
    partes = []
    for nid, info in _nodos_vistos.items():
        edad = utime.ticks_diff(ahora, info.get("ultimo_visto", 0)) // 1000
        if info.get("via_directo", False):
            ruta = "directo"
        else:
            ruta = "via_" + ",".join(info.get("via_relay", []))
        partes.append("{}:{}:{}s".format(nid, ruta, edad))
    cola_subida.append("{},{},STATUS,{}".format(ts, CLIENT_ID, "|".join(partes)))

# ───────────────────────────────────────────────
#  UI
# ───────────────────────────────────────────────
def ui_status(linea1, linea2="", col1=AMARILLO, col2=GRIS):
    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER v17.0", 4, 4,  VERDE)
    tft.write(font_sm, linea1[:28], 4, 28, col1)
    if linea2:
        tft.write(font_sm, linea2[:28], 4, 52, col2)
    backlight.value(1)

def heartbeat():
    h     = hora_local()
    nodos = " ".join(list(_nodos_vistos.keys())[-4:]) if _nodos_vistos else "sin nodos"
    tft.fill(NEGRO)
    tft.write(font_sm, "Master v17.0",  4,   4,  VERDE)
    tft.write(font_sm, h,               148, 4,  GRIS)
    tft.write(font_sm, "T:{}  H:{}".format(_t_propio, _h_propio), 4, 26, AMARILLO)
    tft.write(font_sm, "ch:{}  ntp:{}".format(
        _canal_actual, "ok" if _ntp_ok else "--"), 4, 50, GRIS)
    tft.write(font_sm, nodos[:30], 4, 74, GRIS)
    backlight.value(1)
    utime.sleep_ms(500)
    backlight.value(0)

def pantalla_completa():
    def _e(v):
        if v is True:  return "OK",  VERDE
        if v is False: return "ERR", ROJO
        return                 "---", GRIS
    h     = hora_local()
    nodos = " ".join(list(_nodos_vistos.keys())[-5:]) if _nodos_vistos else "sin nodos"
    ws, wc  = _e(_wif_ok)
    ms, mc  = _e(_mq_ok)
    svs, sc = _e(_svr_ok)
    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER", 4,   2,  VERDE)
    tft.write(font_sm, h,            160, 2,  CYAN)
    tft.write(font_md, "Temp: {}C".format(_t_propio), 4,   22, AMARILLO)
    tft.write(font_md, "Hume: {}%".format(_h_propio), 4,   52, CYAN)
    tft.write(font_sm, nodos[:30], 4, 84, GRIS)
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO); tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO); tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO); tft.write(font_sm, svs, 200, 108, sc)
    backlight.value(1)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER", 4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",   4,  44, CYAN)
    tft.write(font_sm, "v17.0",      4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...",4, 92, BLANCO)
    backlight.value(1)
    utime.sleep_ms(2000)
    backlight.value(0)

# ───────────────────────────────────────────────
#  HTTP — comandos del servidor
# ───────────────────────────────────────────────
def _cmd_a_wave(txt):
    """Convierte 'REQ:ALL' o 'REQ:NODE_ID' o 'PAIR' al JSON de WAVE."""
    if txt == "PAIR":
        # PAIR no genera WAVE — encola medición propia inmediata
        ts = hora_local(con_fecha=True)
        cola_subida.append("{},{},T:{} H:{},sensor".format(
            ts, CLIENT_ID, _t_propio, _h_propio))
        encolar_status(ts)
        print("[PAIR] medición propia encolada")
        return None
    tgt = txt[4:] if (txt.startswith("REQ:") and txt != "REQ:ALL") else "ALL"
    return json.dumps({
        "type":   "WAVE",
        "cmd":    txt,
        "from":   CLIENT_ID,
        "target": tgt,
        "ttl":    6,
        "ch":     _canal_actual,
        "mid":    _next_msg_id()
    })

def consultar_comandos_servidor(timeout_ms=1500):
    """
    HTTP GET al servidor pidiendo comandos pendientes.
    Devuelve lista de comandos string. Encola las WAVEs en cola_bajada.
    """
    try:
        r = urequests.get(URL_COMANDOS, timeout=timeout_ms / 1000.0)
        data = r.json()
        r.close()
    except Exception as e:
        print("[HTTP GET ERR]", e)
        return []

    cmds = data.get("comandos", []) if isinstance(data, dict) else []
    if not cmds:
        return []

    print("[<<< HTTP RX] {} comandos:".format(len(cmds)), cmds)
    for txt in cmds:
        wave = _cmd_a_wave(txt)
        if wave is not None:
            cola_bajada.append(wave)

    # ACK al servidor para que limpie su cola
    try:
        ack = urequests.post(URL_ACK, json={"n": len(cmds)},
                             timeout=timeout_ms / 1000.0)
        ack.close()
    except Exception as e:
        print("[HTTP ACK ERR]", e)
    return cmds

# ───────────────────────────────────────────────
#  VENTANA WIFI — solo MQTT TX para subir datos
# ───────────────────────────────────────────────
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
    except: pass
    print("[WIFI] OK ch:{} ip:{}".format(_canal_actual, sta.ifconfig()[0]))

    if not _ntp_ok:
        try:
            ntptime.host = "pool.ntp.org"
            ntptime.settime()
            _ntp_ok = True
            print("[NTP] OK")
        except Exception as e:
            print("[NTP] Fallo:", e)

    # 1) Consultar comandos pendientes del servidor (HTTP GET)
    consultar_comandos_servidor()

    # 2) Publicar datos al broker MQTT
    client = None
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.connect()
        _mq_ok = True
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

    try: sta.disconnect()
    except: pass
    utime.sleep_ms(200)

# ───────────────────────────────────────────────
#  VENTANA MESH — ESP-NOW
# ───────────────────────────────────────────────
def ventana_mesh(cmd="REQ:ALL", target="ALL"):
    if _en is None:
        print("[MESH] ESP-NOW no disponible")
        return 0

    sta = network.WLAN(network.STA_IF)
    try: sta.config(channel=_canal_actual)
    except: pass
    utime.sleep_ms(150)

    cmd_mostrar = cmd
    mid_actual  = None
    if cola_bajada:
        onda = cola_bajada.pop(0)
        try:
            d = json.loads(onda)
            cmd_mostrar = d.get("cmd", "WAVE")
            mid_actual  = d.get("mid")
        except: pass
        print("[>>> MESH TX] mid:{}  cmd:{}".format(mid_actual, cmd_mostrar))
        ui_status("WAVE servidor", cmd_mostrar[:20], CYAN, AMARILLO)
    else:
        mid_actual = _next_msg_id()
        onda = json.dumps({
            "type":"WAVE","cmd":cmd,"from":CLIENT_ID,"target":target,
            "ttl":6,"ch":_canal_actual,"mid":mid_actual
        })
        print("[>>> MESH TX auto] mid:{}  cmd:{}".format(mid_actual, cmd))

    exitos = 0
    for _ in range(BROADCAST_N):
        try:
            if _en.send(BROADCAST_MAC, onda):
                exitos += 1
        except Exception as e:
            print("[TX ERR]", e)
        utime.sleep_ms(150)
    print("[TX DONE] mid:{}  exitos:{}/{}".format(mid_actual, exitos, BROADCAST_N))
    del onda

    fin = utime.ticks_add(utime.ticks_ms(), T_MESH_LISTEN)
    recibidos = 0
    procesados = set()

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        try: host, msg = _en.recv(10)
        except:
            utime.sleep_ms(10); continue
        if not msg: continue
        try:
            txt  = msg.decode()
            data = json.loads(txt)
            if data.get("type") in ("FEEDBACK", "FB"):
                nodo   = data.get("id", "?")
                par    = data.get("par", "?")
                via    = data.get("via", [])
                mid_fb = data.get("mid", "?")
                if nodo in procesados:
                    print("[DEDUP]", nodo, "via:", via)
                    del data, txt
                    continue
                procesados.add(nodo)
                payload = data.get("payload") or data.get("pl") or []
                ts      = hora_local(con_fecha=True)
                for linea in _payload_csv(ts, nodo, payload):
                    cola_subida.append(linea)
                if nodo not in _nodos_vistos:
                    _nodos_vistos[nodo] = {"count": 0}
                info = _nodos_vistos[nodo]
                info["ultimo_visto"] = utime.ticks_ms()
                info["parent"]       = par
                info["count"]        = info.get("count", 0) + 1
                info["via_directo"]  = (len(via) == 0)
                info["via_relay"]    = via
                if len(_nodos_vistos) > 15:
                    oldest = min(_nodos_vistos.keys(),
                                 key=lambda k: _nodos_vistos[k].get("ultimo_visto", 0))
                    if oldest != nodo:
                        del _nodos_vistos[oldest]
                recibidos += 1
                ruta = "directo" if not via else "via " + ",".join(via)
                print("[<<< FB RX] nodo:{}  par:{}  mid:{}  items:{}  ruta:{}".format(
                    nodo, par, mid_fb, len(payload), ruta))
            del data, txt
        except Exception as e:
            print("[RX ERR]", e)

    print("[MESH OK] recibidos:", recibidos)
    gc.collect()
    return recibidos

# ───────────────────────────────────────────────
#  CHECK RÁPIDO HTTP — push prioritario
# ───────────────────────────────────────────────
def check_http_rapido():
    """
    Tras mesh: reconectar WiFi mínimo y consultar HTTP por comandos.
    Si hay comando, ejecutar mesh extra inmediato.
    Latencia botón→nodo: ~2-3s (en lugar de ~5-10s del ciclo normal).
    """
    sta = network.WLAN(network.STA_IF)
    if not sta.isconnected():
        try:
            sta.connect(WIFI_SSID, WIFI_PASS)
            fin = utime.ticks_add(utime.ticks_ms(), 3_000)
            while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
                if sta.isconnected(): break
                utime.sleep_ms(200)
        except: pass
    if not sta.isconnected():
        return
    cmds = consultar_comandos_servidor(timeout_ms=1000)
    try: sta.disconnect()
    except: pass
    if cmds:
        utime.sleep_ms(200)
        reset_radio()
        ui_status("WAVE prioritaria", cmds[0][:20], CYAN, AMARILLO)
        ventana_mesh()
        reset_radio()

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

        # ── Botón servidor (medición propia) ──
        if _flag_server:
            _flag_server = False
            ui_status("Servidor", "publicando...", AMARILLO, GRIS)
            ts = hora_local(con_fecha=True)
            cola_subida.append("{},{},T:{} H:{},sensor".format(
                ts, CLIENT_ID, _t_propio, _h_propio))
            encolar_status(ts)
            ventana_wifi()
            medir_propio()
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_MS)
            backlight.value(0)
            t_ultimo_10min = t_ultimo_hb = utime.ticks_ms()
            continue

        # ── Botón mesh (WAVE manual) ──
        if _flag_mesh:
            _flag_mesh = False
            ui_status("Malla", "WAVE manual...", AMARILLO, GRIS)
            ventana_mesh(cmd="REQ:ALL", target="ALL")
            reset_radio()
            if cola_subida:
                ventana_wifi()
                reset_radio()
            t_ultimo_hb = utime.ticks_ms()
            continue

        # ── Ciclo normal ──
        medir_propio()
        ventana_wifi()
        reset_radio()

        if cola_bajada:
            backlight.value(1)
            ui_status("Comando recibido", "ejecutando...", CYAN, AMARILLO)

        ventana_mesh()
        reset_radio()

        # PUSH PRIORITARIO: tras mesh, check HTTP rápido por comandos nuevos
        check_http_rapido()

        # Flush si quedaron datos
        if cola_subida:
            print("[FLUSH] Publicando", len(cola_subida), "datos")
            ts_status = hora_local(con_fecha=True)
            encolar_status(ts_status)
            ventana_wifi()
            reset_radio()

        if backlight.value() == 1:
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_MS)
            backlight.value(0)

        t_ultimo_hb = utime.ticks_ms()

        # Pantalla cada 10 min
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_10min) >= T_10MIN:
            ts = hora_local(con_fecha=True)
            cola_subida.append("{},{},T:{} H:{},sensor".format(
                ts, CLIENT_ID, _t_propio, _h_propio))
            encolar_status(ts)
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_MS)
            backlight.value(0)
            t_ultimo_10min = t_ultimo_hb = utime.ticks_ms()

        # Heartbeat
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
    tft.write(font_md, "RADIO ERR",       4,  8,  ROJO)
    tft.write(font_sm, "Desconectar USB", 4,  50, AMARILLO)
    tft.write(font_sm, "Power cycle",     4,  74, AMARILLO)
    backlight.value(1)
    while True:
        utime.sleep_ms(5000)

print("[OK] Master v17.0 listo")

while True:
    try:
        loop()
    except Exception as e:
        print("[FATAL]", e)
        tft.fill(NEGRO)
        tft.write(font_md, "ERROR",       4,  8,  ROJO)
        tft.write(font_sm, str(e)[:26],   4,  50, BLANCO)
        tft.write(font_sm, "soft reset",  4,  80, AMARILLO)
        backlight.value(1)
        utime.sleep_ms(3000)
        backlight.value(0)
        machine.reset()
