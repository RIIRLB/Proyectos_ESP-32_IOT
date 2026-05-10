# ============================================================
#  MASTER_TTGO v12.2 — PIF Mesh / LAB-ARTE
#
#  Causa raíz definitiva del 0x0101:
#    WiFi necesita 10 rx buffers. ESP-NOW necesita 4.
#    Si ESP-NOW corrió último antes de un reset (SW o HW),
#    el hardware queda con 4 buffers. Al reiniciar Python,
#    WiFi intenta tomar 10, encuentra 4 → 0x0101.
#
#  Solución en tres reglas:
#    1. Al arrancar: cerrar ESP-NOW primero (libera 4 buffers)
#       LUEGO inicializar WiFi (toma 10 buffers limpio)
#    2. WiFi driver (_sta) vive para siempre — NUNCA active(False)
#    3. ESP-NOW corre SOBRE el WiFi driver (sin reasignar buffers)
#       en.active(False) al final de cada mesh — seguro porque
#       cierra solo la capa de protocolo, no los buffers base
#
#  Sin lightsleep entre fases — ya no es necesario porque
#  los buffers no cambian entre WiFi y ESP-NOW.
#
#  Ciclo:
#    [startup: cleanup + wifi init] →
#    ventana_wifi(5s) → ventana_mesh(5s) → repite
#
#  Pantalla: heartbeat 80ms cada 5s | completa cada 10min
#  Botones IRQ:
#    BTN35 (GPIO35) → mide + sube servidor ahora
#    BTN0  (GPIO0)  → WAVE a todos ahora
#  Desde Raspi MQTT:
#    "REQ:ALL"      → WAVE a todos
#    "REQ:SLAVE_01" → WAVE solo a ese nodo
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

T_WIFI_CONNECT = 8_000    # Timeout conexión WiFi (ms)
T_MESH_LISTEN  = 5_000    # Ventana escucha ESP-NOW (ms)
T_HEARTBEAT    = 5_000    # Parpadeo de vida (ms)
T_10MIN        = 600_000  # Pantalla completa cada 10 min (ms)
DISPLAY_ON_S   = 15       # Segundos que la pantalla completa queda ON
BROADCAST_N    = 3        # Repeticiones de WAVE

# ───────────────────────────────────────────────
#  HARDWARE (no-radio)
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
hw        = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(0)

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
#  COLAS Y ESTADO
# ───────────────────────────────────────────────
cola_subida   = []
cola_bajada   = []
_nodos_vistos = []
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
#  INIT DE RADIO — solo se llama UNA vez en arranque_seguro()
#
#  Regla 1: cerrar ESP-NOW primero (libera 4 buffers si estaba activo)
#  Regla 2: inicializar WiFi (10 buffers) — queda vivo para siempre
#  Regla 3: NUNCA llamar _sta.active(False) después de aquí
# ───────────────────────────────────────────────
_sta = None   # Se asigna en arranque_seguro()

def arranque_seguro():
    """
    Inicialización de radio robusta contra cualquier estado previo.
    Devuelve True si el radio quedó listo.
    """
    global _sta

    print("[RADIO] Limpiando estado previo...")

    # Paso 1: cerrar ESP-NOW si estaba activo (libera 4 buffers)
    # en.active(False) es seguro — solo cierra la capa de protocolo
    try:
        en_tmp = espnow.ESPNow()
        en_tmp.active(False)
        del en_tmp
        gc.collect()
        utime.sleep_ms(300)
        print("[RADIO] ESP-NOW cerrado OK")
    except Exception as e:
        print("[RADIO] ESP-NOW cleanup:", e)   # no era activo — OK

    # Paso 2: inicializar WiFi con 10 buffers
    # Con ESP-NOW cerrado, no hay conflicto de buffers
    try:
        _sta = network.WLAN(network.STA_IF)
        if not _sta.active():
            _sta.active(True)
            utime.sleep_ms(500)
        print("[RADIO] WiFi driver activo")
        return True
    except Exception as e:
        print("[RADIO] WiFi init falló:", e)
        return False

# ───────────────────────────────────────────────
#  UI
# ───────────────────────────────────────────────
def _est(val):
    if val is True:  return "OK",  VERDE
    if val is False: return "ERR", ROJO
    return                   "---", GRIS

def heartbeat():
    """Parpadeo 80ms — señal de vida. Se llama cada 5s en reposo."""
    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    nodos = " ".join(_nodos_vistos[-5:]) if _nodos_vistos else "sin nodos"
    tft.fill(NEGRO)
    tft.write(font_sm, "PIF v12.2", 4,   8,  VERDE)
    tft.write(font_sm, hora,        140, 8,  GRIS)
    tft.write(font_sm, nodos[:30],  4,   36, GRIS)
    backlight.value(1)
    utime.sleep_ms(80)
    backlight.value(0)

def pantalla_completa():
    """Pantalla grande T/H + nodos + estado. Caller la apaga."""
    ws, wc   = _est(_wif_ok)
    ms, mc   = _est(_mq_ok)
    svs, svc = _est(_svr_ok)
    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    nodos = " ".join(_nodos_vistos[-5:]) if _nodos_vistos else "sin nodos aun"

    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER",               4,   2,  VERDE)
    tft.write(font_sm, hora,                       160, 2,  CYAN)
    tft.write(font_md, "T: {}C".format(_t_propio),  4,  22, AMARILLO)
    tft.write(font_md, "H: {}%".format(_h_propio),  4,  52, CYAN)
    tft.write(font_sm, nodos[:30],                  4,  84, GRIS)
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO);  tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO);  tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO);  tft.write(font_sm, svs, 200, 108, svc)
    backlight.value(1)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v12.2",        4,  68, AMARILLO)
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
    """T y H del mismo nodo en una sola línea CSV."""
    t_val = h_val = None
    otros = []
    for m in payload:
        tipo = m.get("t") or m.get("tipo", "?")
        val  = m.get("v") if m.get("v") is not None else m.get("val", "?")
        if   tipo == "Temp": t_val = val
        elif tipo == "Hum":  h_val = val
        else:                otros.append((tipo, val))
    lineas = []
    if t_val is not None or h_val is not None:
        ts = "T:{}".format(t_val) if t_val is not None else "T:?"
        hs = "H:{}".format(h_val) if h_val is not None else "H:?"
        lineas.append("{},{},{} {},sensor".format(hora, nodo, ts, hs))
    for tipo, val in otros:
        lineas.append("{},{},{},{}".format(hora, nodo, tipo, val))
    return lineas

# ───────────────────────────────────────────────
#  VENTANA WIFI
#  _sta ya está activo (arranque_seguro lo inició).
#  Solo connect() / MQTT / disconnect().
#  NUNCA _sta.active(False).
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

    # WiFi driver ya está activo — solo conectar al AP
    if not _sta.isconnected():
        _sta.connect(WIFI_SSID, WIFI_PASS)
        fin = utime.ticks_add(utime.ticks_ms(), T_WIFI_CONNECT)
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
        client.check_msg()   # comandos Raspi → cola_bajada

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

    # Soltar AP — WiFi driver sigue activo, listo para ESP-NOW
    try: _sta.disconnect()
    except: pass
    utime.sleep_ms(200)

# ───────────────────────────────────────────────
#  VENTANA MESH
#  El WiFi driver ya tiene 10 buffers activos.
#  ESP-NOW se activa ENCIMA sin cambiar los buffers base.
#  en.active(False) al final cierra solo el protocolo ESP-NOW —
#  seguro porque no toca el driver WiFi subyacente.
# ───────────────────────────────────────────────
def ventana_mesh(cmd="REQ:ALL", target="ALL"):
    global _nodos_vistos

    # Canal 6 para ESP-NOW — slaves deben usar el mismo
    _sta.config(channel=6)

    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    # WAVE: custom de cola_bajada o default
    if cola_bajada:
        onda = cola_bajada.pop(0)
        print("[MESH TX custom]", onda[:60])
    else:
        onda = json.dumps({
            "type"  : "WAVE",
            "cmd"   : cmd,
            "from"  : CLIENT_ID,
            "target": target,
            "ttl"   : 6
        })
        print("[MESH TX default] cmd:", cmd)

    for _ in range(BROADCAST_N):
        try: en.send(BROADCAST_MAC, onda)
        except Exception as e: print("[TX ERR]", e)
        utime.sleep_ms(150)
    del onda

    # Escuchar FBs
    fin = utime.ticks_add(utime.ticks_ms(), T_MESH_LISTEN)
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
                    if len(_nodos_vistos) > 10:
                        _nodos_vistos.pop(0)
                recibidos += 1
            del data, txt
        except Exception as e:
            print("[RX ERR]", e)

    print("[MESH OK] nodos:", recibidos)

    # Cerrar capa de protocolo ESP-NOW — WiFi driver sigue activo
    en.active(False)
    del en
    gc.collect()
    return recibidos

# ───────────────────────────────────────────────
#  LOOP PRINCIPAL
# ───────────────────────────────────────────────
def loop():
    global _flag_server, _flag_mesh

    t_ultimo_hb    = utime.ticks_ms()
    t_ultimo_10min = utime.ticks_ms() - T_10MIN   # pantalla al arrancar

    while True:
        gc.collect()

        # ── Botón servidor ──────────────────────
        if _flag_server:
            _flag_server = False
            print("[BTN] Servidor")
            t, h = medir_propio()
            hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
            if t != "Err":
                cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))
            ventana_wifi()
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_S * 1000)
            backlight.value(0)
            t_ultimo_10min = t_ultimo_hb = utime.ticks_ms()
            continue

        # ── Botón mesh ──────────────────────────
        if _flag_mesh:
            _flag_mesh = False
            print("[BTN] Mesh")
            ventana_mesh(cmd="REQ:ALL", target="ALL")
            t_ultimo_hb = utime.ticks_ms()
            continue

        # ── Ciclo normal: wifi → mesh ───────────
        ventana_wifi()
        ventana_mesh()
        t_ultimo_hb = utime.ticks_ms()

        # ── Pantalla cada 10 min ────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_10min) >= T_10MIN:
            t, h = medir_propio()
            hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
            if t != "Err":
                cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_S * 1000)
            backlight.value(0)
            t_ultimo_10min = t_ultimo_hb = utime.ticks_ms()

        # ── Heartbeat cada 5s ───────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_hb) >= T_HEARTBEAT:
            heartbeat()
            t_ultimo_hb = utime.ticks_ms()

        print("[RAM]", gc.mem_free(),
              "| sub:", len(cola_subida),
              "| nodos:", len(_nodos_vistos))

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
gc.collect()

# Init de radio seguro — maneja cualquier estado previo
if not arranque_seguro():
    # Si WiFi no pudo inicializarse, hard reset para limpiar hardware
    tft.fill(NEGRO)
    tft.write(font_md, "RADIO ERR",    4, 8,  ROJO)
    tft.write(font_sm, "hard reset...", 4, 50, AMARILLO)
    backlight.value(1)
    utime.sleep_ms(3000)
    machine.reset()

print("[OK] Radio listo — iniciando loop")
backlight.value(0)

while True:
    try:
        loop()
    except Exception as e:
        print("[FATAL]", e)
        tft.fill(NEGRO)
        tft.write(font_md, "ERROR",       4,  8,  ROJO)
        tft.write(font_sm, str(e)[:26],   4,  50, BLANCO)
        tft.write(font_sm, "reset en 5s", 4,  80, AMARILLO)
        backlight.value(1)
        utime.sleep_ms(5000)
        backlight.value(0)
        # Hard reset: limpia el hardware de radio completamente.
        # Soft reboot (lo que haría un except vacío) NO limpia el hardware.
        machine.reset()
