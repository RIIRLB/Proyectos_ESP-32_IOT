# ============================================================
#  MASTER_TTGO v11.0 — PIF Mesh / LAB-ARTE
#
#  Sin lightsleep — el master corre en loop continuo.
#  El display se gestiona por software: OFF la mayor parte
#  del tiempo, parpadeo de 80ms cada ciclo de radio para
#  mostrar mini-status, y pantalla completa cada 10 min.
#
#  Radio: idéntico a v7.2 (funcional y probado)
#    WiFi  → sta.active(True) → ... → sta.disconnect()
#    ESP-NOW → en.active(True) → ... → en.active(False)
#    NUNCA sta.active(False)
#
#  Flujo por ciclo (~10s):
#    ventana_mesh(5s) → mini_display → ventana_wifi(5s) → mini_display
#    cada 10min → pantalla_completa(15s) → display OFF
#
#  Botones (IRQ):
#    BTN35 (GPIO35) → mide propio + sube servidor
#    BTN0  (GPIO0)  → WAVE a todos los slaves
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

T_MESH_MS    = 5_000    # Ventana escucha ESP-NOW
T_WIFI_MS    = 8_000    # Timeout conexión WiFi
T_10MIN_MS   = 600_000  # 10 minutos en ms
BROADCAST_N  = 3        # Repeticiones de WAVE
DISPLAY_ON_S = 15       # Segundos que se queda la pantalla completa

# ───────────────────────────────────────────────
#  COLAS
# ───────────────────────────────────────────────
cola_subida = []   # datos malla → Raspberry (CSV)
cola_bajada = []   # órdenes Raspi → malla (JSON WAVE)

# ───────────────────────────────────────────────
#  ESTADO GLOBAL
# ───────────────────────────────────────────────
_t_propio     = "--"
_h_propio     = "--"
_nodos_vistos = []       # lista de IDs de nodos detectados en últimos ciclos
_t_ultimo_10min = 0      # ticks del último display completo

# ───────────────────────────────────────────────
#  FLAGS IRQ — solo escritura en ISR
# ───────────────────────────────────────────────
_flag_server = False   # BTN35 → mide + sube servidor
_flag_mesh   = False   # BTN0  → WAVE a todos

def _isr_server(pin):
    global _flag_server
    _flag_server = True

def _isr_mesh(pin):
    global _flag_mesh
    _flag_mesh = True

# ───────────────────────────────────────────────
#  HARDWARE
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
hw        = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(0)   # Display OFF al arrancar

btn_env = Pin(35, Pin.IN, Pin.PULL_UP)
btn_dir = Pin(0,  Pin.IN, Pin.PULL_UP)
btn_env.irq(trigger=Pin.IRQ_FALLING, handler=_isr_server)
btn_dir.irq(trigger=Pin.IRQ_FALLING, handler=_isr_mesh)

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
#  UI — MINI DISPLAY (parpadeo rápido)
#
#  Solo enciende el backlight 80ms, escribe 2 líneas,
#  apaga. No molesta pero confirma que el sistema vive.
#  Layout compacto:
#    [modo]  [hora]
#    [info]
#    [nodos: N1 N2 ...]
# ───────────────────────────────────────────────
def mini_display(modo, info, col=BLANCO):
    """Parpadeo de 80ms con estado actual."""
    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)

    # Nodos en una línea corta
    if _nodos_vistos:
        nodos_txt = " ".join(_nodos_vistos[-4:])   # max 4 IDs
    else:
        nodos_txt = "sin nodos"

    tft.fill(NEGRO)
    tft.write(font_sm, modo,      4,   4,  VERDE)
    tft.write(font_sm, hora,      155, 4,  GRIS)
    tft.write(font_sm, info[:26], 4,   28, col)
    tft.write(font_sm, nodos_txt[:30], 4, 52, GRIS)

    backlight.value(1)
    utime.sleep_ms(80)
    backlight.value(0)

# ───────────────────────────────────────────────
#  UI — PANTALLA COMPLETA (cada 10 min o botón)
#
#  Se muestra durante DISPLAY_ON_S segundos y luego
#  el loop apaga el backlight automáticamente.
#  Layout:
#    PIF MASTER          [hora]   font_sm
#    T: 23C                       font_md GRANDE
#    H: 65%                       font_md GRANDE
#    [nodos conectados]           font_sm
#    WiFi:OK  MQTT:OK  Svr:OK    font_sm
# ───────────────────────────────────────────────
def pantalla_completa(wif=None, mq=None, svr=None, msg=""):
    def _e(v):
        if v is True:  return "OK",  VERDE
        if v is False: return "ERR", ROJO
        return               "---",  GRIS

    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    ws, wc   = _e(wif)
    ms, mc   = _e(mq)
    svs, svc = _e(svr)

    nodos_txt = " ".join(_nodos_vistos[-5:]) if _nodos_vistos else "sin nodos aun"

    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER",              4,   2,  VERDE)
    tft.write(font_sm, hora,                      160, 2,  CYAN)
    tft.write(font_md, "T: {}C".format(_t_propio), 4,  22, AMARILLO)
    tft.write(font_md, "H: {}%".format(_h_propio), 4,  52, CYAN)
    tft.write(font_sm, nodos_txt[:30],             4,  84, GRIS)
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO); tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO); tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO); tft.write(font_sm, svs, 200, 108, svc)

    backlight.value(1)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v11.0",        4,  68, AMARILLO)
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
        ts = "T:{}".format(t_val) if t_val is not None else "T:?"
        hs = "H:{}".format(h_val) if h_val is not None else "H:?"
        lineas.append("{},{},{} {},sensor".format(hora, nodo, ts, hs))
    for tipo, val in otros:
        lineas.append("{},{},{},{}".format(hora, nodo, tipo, val))
    return lineas

# ───────────────────────────────────────────────
#  VENTANA MESH — radio v7.2
# ───────────────────────────────────────────────
def ventana_mesh(cmd="REQ:ALL", target="ALL"):
    global _nodos_vistos

    mini_display("MESH TX", "Enviando WAVE...", AMARILLO)

    # Radio — idéntico a v7.2
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=6)
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

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

    # Enviar
    for _ in range(BROADCAST_N):
        try: en.send(BROADCAST_MAC, onda)
        except Exception as e: print("[TX ERR]", e)
        utime.sleep_ms(150)
    print("[MESH TX]", onda[:60])
    del onda

    # Escuchar FBs
    mini_display("MESH RX", "Escuchando {}s...".format(T_MESH_MS//1000), CYAN)
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
                if nodo not in _nodos_vistos:
                    _nodos_vistos.append(nodo)
                    if len(_nodos_vistos) > 10:   # max 10 en memoria
                        _nodos_vistos.pop(0)
                recibidos += 1
                mini_display("MESH RX", "Nodo: " + nodo, VERDE)
            del data, txt
        except Exception as e:
            print("[RX ERR]", e)

    mini_display("MESH OK", "Nodos: {}".format(recibidos), VERDE)

    # Transición — idéntico a v7.2
    en.active(False)
    utime.sleep_ms(200)
    gc.collect()
    return recibidos

# ───────────────────────────────────────────────
#  VENTANA WIFI+MQTT — radio v7.2
# ───────────────────────────────────────────────
def _mqtt_cb(topic, msg):
    txt = msg.decode().strip()
    print("[MQTT RX]", txt)
    # Formato: "REQ:ALL" o "REQ:SLAVE_01"
    if txt.startswith("REQ:") and txt != "REQ:ALL":
        tgt = txt[4:]
    else:
        tgt = "ALL"
    cola_bajada.append(json.dumps({
        "type"  : "WAVE",
        "cmd"   : txt,
        "from"  : CLIENT_ID,
        "target": tgt,
        "ttl"   : 6
    }))

def ventana_wifi():
    mini_display("WIFI", "Conectando...", AMARILLO)

    # Radio — idéntico a v7.2
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=6)

    wif = mq = svr = False

    if not sta.isconnected():
        sta.connect(WIFI_SSID, WIFI_PASS)
        fin = utime.ticks_add(utime.ticks_ms(), T_WIFI_MS)
        while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
            if sta.isconnected(): break
            utime.sleep_ms(400)

    if not sta.isconnected():
        mini_display("WIFI ERR", "Sin conexion", ROJO)
        try: sta.disconnect()
        except: pass
        utime.sleep_ms(200)
        return False, False, False

    wif = True
    mini_display("WIFI OK", sta.ifconfig()[0], VERDE)

    client = None
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(_mqtt_cb)
        client.connect()
        mq = True

        client.subscribe(TOPIC_SUB)
        client.check_msg()   # comandos Raspi → cola_bajada

        enviados = 0
        while cola_subida:
            item = cola_subida.pop(0)
            client.publish(TOPIC_PUB, item)
            enviados += 1
            print("[MQTT TX]", item)

        svr = True
        mini_display("WIFI OK", "Enviados:{}".format(enviados), VERDE)
        utime.sleep_ms(300)

    except Exception as e:
        print("[MQTT ERR]", e)
        mini_display("MQTT ERR", str(e)[:20], ROJO)
        utime.sleep_ms(500)
    finally:
        if client:
            try: client.disconnect()
            except: pass
            del client
        gc.collect()

    # Transición — idéntico a v7.2
    try: sta.disconnect()
    except: pass
    utime.sleep_ms(200)
    return wif, mq, svr

# ───────────────────────────────────────────────
#  LOOP PRINCIPAL — sin lightsleep
# ───────────────────────────────────────────────
def loop():
    global _flag_server, _flag_mesh, _t_ultimo_10min

    _t_ultimo_10min = utime.ticks_ms()   # forzar display al arrancar

    while True:
        ahora = utime.ticks_ms()
        gc.collect()

        # ── ¿Botón servidor? ────────────────────
        if _flag_server:
            _flag_server = False
            mini_display("BTN SRV", "Midiendo...", AMARILLO)

            t, h = medir_propio()
            hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
            if t != "Err":
                cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))
            wif, mq, svr = ventana_wifi()
            pantalla_completa(wif=wif, mq=mq, svr=svr)
            utime.sleep_ms(DISPLAY_ON_S * 1000)
            backlight.value(0)
            _t_ultimo_10min = utime.ticks_ms()   # reiniciar contador
            continue

        # ── ¿Botón mesh? ────────────────────────
        if _flag_mesh:
            _flag_mesh = False
            mini_display("BTN MESH", "WAVE a todos...", AMARILLO)
            ventana_mesh(cmd="REQ:ALL", target="ALL")
            continue

        # ── Ciclo normal: mesh → wifi ───────────
        try:
            ventana_mesh()
        except Exception as e:
            print("[MESH ERR]", e)
            mini_display("MESH ERR", str(e)[:20], ROJO)
            gc.collect()

        try:
            wif, mq, svr = ventana_wifi()
        except Exception as e:
            print("[WIFI ERR]", e)
            mini_display("WIFI ERR", str(e)[:20], ROJO)
            wif = mq = svr = False
            gc.collect()

        # ── ¿Toca pantalla completa de 10 min? ──
        if utime.ticks_diff(utime.ticks_ms(), _t_ultimo_10min) >= T_10MIN_MS:
            t, h = medir_propio()
            hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
            if t != "Err":
                cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))

            pantalla_completa(wif=wif, mq=mq, svr=svr)
            utime.sleep_ms(DISPLAY_ON_S * 1000)
            backlight.value(0)
            _t_ultimo_10min = utime.ticks_ms()

        print("[RAM]", gc.mem_free(), "| cola_sub:", len(cola_subida),
              "| nodos:", len(_nodos_vistos))

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
        tft.write(font_md, "ERROR",      4, 8,  ROJO)
        tft.write(font_sm, str(e)[:26],  4, 50, BLANCO)
        backlight.value(1)
        utime.sleep_ms(5000)
        backlight.value(0)
        gc.collect()
