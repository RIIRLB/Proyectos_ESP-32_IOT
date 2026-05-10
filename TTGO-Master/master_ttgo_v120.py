# ============================================================
#  MASTER_TTGO v12.0 — PIF Mesh / LAB-ARTE
#
#  Causa raíz del 0x0101 (explicación definitiva):
#    WiFi necesita 10 rx buffers de radio.
#    ESP-NOW necesita 4 rx buffers de radio.
#    Son incompatibles si uno está activo cuando arranca el otro.
#    El lightsleep resetea el hardware de radio → buffers a cero
#    → el siguiente modo puede iniciar desde cero sin conflicto.
#
#  Solución: lightsleep(200ms) ENTRE fases como reset de radio.
#    ventana_wifi(5s) → lightsleep(200ms) → ventana_mesh(5s) → lightsleep(200ms)
#    El 200ms no es ahorro de energía — es limpieza de hardware.
#
#  Radio: idéntico a v7.2 (el único que funcionó):
#    WiFi  → sta.active(True) → connect() → ... → disconnect() → lightsleep
#    ESP-NOW → en.active(True) → send/recv → en.active(False) → lightsleep
#    NUNCA sta.active(False)
#    Objetos de radio son LOCALES a cada función, no globales.
#
#  Heartbeat: parpadeo 80ms cada 5s solo cuando está en reposo.
#  Pantalla completa: cada 10 min, 15s visible.
#  Botones IRQ:
#    BTN35 (GPIO35) → fuerza ciclo servidor ahora
#    BTN0  (GPIO0)  → fuerza ciclo mesh ahora
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
T_RADIO_RESET  = 200      # lightsleep entre fases — reset de buffers de radio
T_HEARTBEAT    = 5_000    # Parpadeo de vida (ms)
T_10MIN        = 600_000  # Pantalla completa cada 10 min (ms)
DISPLAY_ON_S   = 15       # Segundos que se queda ON la pantalla completa
BROADCAST_N    = 3        # Repeticiones de WAVE

# ───────────────────────────────────────────────
#  COLAS
# ───────────────────────────────────────────────
cola_subida = []   # datos malla → Raspberry (CSV)
cola_bajada = []   # órdenes Raspi → malla (JSON WAVE)

# ───────────────────────────────────────────────
#  ESTADO
# ───────────────────────────────────────────────
_t_propio     = "--"
_h_propio     = "--"
_nodos_vistos = []    # IDs detectados, máx 10
_wif_ok       = None
_mq_ok        = None
_svr_ok       = None

# ───────────────────────────────────────────────
#  HARDWARE
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
#  FLAGS IRQ — escritura solo en ISR
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
    """
    Parpadeo 80ms — señal de vida.
    Solo se llama cuando el sistema está en reposo entre ciclos.
    Muestra hora y nodos detectados brevemente.
    """
    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    nodos = " ".join(_nodos_vistos[-5:]) if _nodos_vistos else "sin nodos"
    tft.fill(NEGRO)
    tft.write(font_sm, "PIF v12",    4,   8,  VERDE)
    tft.write(font_sm, hora,         140, 8,  GRIS)
    tft.write(font_sm, nodos[:30],   4,   36, GRIS)
    backlight.value(1)
    utime.sleep_ms(80)
    backlight.value(0)

def pantalla_completa():
    """
    Pantalla grande T/H + nodos + estado WiFi/MQTT/Svr.
    Se queda ON — el caller la apaga después de DISPLAY_ON_S segundos.
    """
    ws, wc   = _est(_wif_ok)
    ms, mc   = _est(_mq_ok)
    svs, svc = _est(_svr_ok)
    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    nodos = " ".join(_nodos_vistos[-5:]) if _nodos_vistos else "sin nodos aun"

    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER",                4,   2,  VERDE)
    tft.write(font_sm, hora,                        160, 2,  CYAN)
    tft.write(font_md, "T: {}C".format(_t_propio),  4,   22, AMARILLO)
    tft.write(font_md, "H: {}%".format(_h_propio),  4,   52, CYAN)
    tft.write(font_sm, nodos[:30],                  4,   84, GRIS)
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO);  tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO);  tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO);  tft.write(font_sm, svs, 200, 108, svc)
    backlight.value(1)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v12.0",        4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)
    backlight.value(1)
    utime.sleep_ms(2000)
    backlight.value(0)

# ───────────────────────────────────────────────
#  RESET DE RADIO
#  lightsleep(200ms) baja el hardware de radio completamente.
#  Al despertar los buffers están en cero y el siguiente
#  modo (WiFi o ESP-NOW) puede iniciar sin conflicto.
#  Es idéntico al mecanismo que hacía v7.2 con lightsleep(15s),
#  pero mucho más corto — solo para limpiar el hardware.
# ───────────────────────────────────────────────
def reset_radio():
    machine.lightsleep(T_RADIO_RESET)

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
#  VENTANA WIFI — idéntico a v7.2
#
#  1. sta.active(True)  — arranca WiFi con 10 rx buffers
#  2. sta.connect()     — conectar al AP
#  3. MQTT TX/RX
#  4. sta.disconnect()  — soltar AP, radio queda ON
#  5. [caller llama reset_radio() → lightsleep 200ms]
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

    # ── Igual que v7.2 ──────────────────────────
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=6)

    if not sta.isconnected():
        sta.connect(WIFI_SSID, WIFI_PASS)
        fin = utime.ticks_add(utime.ticks_ms(), T_WIFI_CONNECT)
        while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
            if sta.isconnected(): break
            utime.sleep_ms(300)

    if not sta.isconnected():
        print("[WIFI] Sin conexion")
        try: sta.disconnect()
        except: pass
        return

    _wif_ok = True
    print("[WIFI] Conectado:", sta.ifconfig()[0])

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

    # ── Igual que v7.2: soltar AP, dejar radio ON ──
    try: sta.disconnect()
    except: pass
    utime.sleep_ms(200)

# ───────────────────────────────────────────────
#  VENTANA MESH — idéntico a v7.2
#
#  Después del lightsleep(200ms), el radio arrancó limpio.
#  1. sta.active(True)  — reactiva el radio (ahora con 4 rx buffers para ESP-NOW)
#  2. en.active(True)   — inicializa ESP-NOW
#  3. send/recv
#  4. en.active(False)  — libera ESP-NOW
#  5. [caller llama reset_radio() → lightsleep 200ms]
# ───────────────────────────────────────────────
def ventana_mesh(cmd="REQ:ALL", target="ALL"):
    global _nodos_vistos

    # ── Igual que v7.2 ──────────────────────────
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=6)

    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    # Construir WAVE (custom de cola o default)
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

    # ── Igual que v7.2 ──────────────────────────
    en.active(False)
    utime.sleep_ms(200)
    gc.collect()
    return recibidos

# ───────────────────────────────────────────────
#  LOOP PRINCIPAL
#
#  Ciclo normal:
#    ventana_wifi → reset_radio → ventana_mesh → reset_radio → repite
#
#  Heartbeat: parpadeo 80ms cada 5s cuando está entre ciclos.
#  Pantalla completa: cada 10 min, se queda 15s.
#
#  Botones:
#    BTN35 → mide + sube servidor + pantalla completa
#    BTN0  → WAVE a todos
# ───────────────────────────────────────────────
def loop():
    global _flag_server, _flag_mesh

    t_ultimo_hb    = utime.ticks_ms()
    t_ultimo_10min = utime.ticks_ms() - T_10MIN   # forzar pantalla al arrancar

    while True:
        gc.collect()
        ahora = utime.ticks_ms()

        # ── Botón servidor ──────────────────────
        if _flag_server:
            _flag_server = False
            print("[BTN] Forzar servidor")

            t, h = medir_propio()
            hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
            if t != "Err":
                cola_subida.append("{},{},T:{} H:{},sensor".format(hora, CLIENT_ID, t, h))

            ventana_wifi()
            reset_radio()
            pantalla_completa()
            utime.sleep_ms(DISPLAY_ON_S * 1000)
            backlight.value(0)
            t_ultimo_10min = utime.ticks_ms()
            t_ultimo_hb    = utime.ticks_ms()
            continue

        # ── Botón mesh ──────────────────────────
        if _flag_mesh:
            _flag_mesh = False
            print("[BTN] Forzar mesh")
            reset_radio()
            ventana_mesh(cmd="REQ:ALL", target="ALL")
            reset_radio()
            t_ultimo_hb = utime.ticks_ms()
            continue

        # ── Ciclo normal: wifi → reset → mesh → reset
        ventana_wifi()
        reset_radio()
        ventana_mesh()
        reset_radio()
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
            t_ultimo_10min = utime.ticks_ms()
            t_ultimo_hb    = utime.ticks_ms()

        # ── Heartbeat ───────────────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_hb) >= T_HEARTBEAT:
            heartbeat()
            t_ultimo_hb = utime.ticks_ms()

        print("[RAM]", gc.mem_free(), "| sub:", len(cola_subida),
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
        tft.write(font_md, "ERROR",       4,  8,  ROJO)
        tft.write(font_sm, str(e)[:26],   4,  50, BLANCO)
        tft.write(font_sm, "reset en 5s", 4,  80, AMARILLO)
        backlight.value(1)
        utime.sleep_ms(5000)
        backlight.value(0)
        # Hard reset: limpia buffers de radio desde cero.
        # Soft reboot NO hace esto — deja los buffers en estado
        # inconsistente y el siguiente ciclo vuelve a fallar.
        machine.reset()
