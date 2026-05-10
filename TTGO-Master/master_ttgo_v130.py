# ============================================================
#  MASTER_TTGO v13.0 — PIF Mesh / LAB-ARTE
#
#  Cambios vs v12.2:
#
#  [1] RESPUESTA SERVIDOR ≤ 5s
#      T_WIFI_CONNECT = 4s + T_MESH_LISTEN = 4s → ciclo ≤ 8s.
#      En práctica WiFi conecta en 1-2s → ciclo ~6s.
#      Comando del servidor → WAVE a slaves en la siguiente
#      ventana_mesh, que arranca ≤ 4s después.
#
#  [2] BOTONES CON FEEDBACK INMEDIATO
#      La ISR enciende el backlight en el momento del press.
#      El texto de acción aparece en ~100ms.
#      La ventana actual termina limpiamente antes de ejecutar
#      la acción del botón (máx 4s de espera real).
#
#  [3] SENSOR SIEMPRE FRESCO
#      DHT11 se lee al inicio de cada ciclo (~cada 8s).
#      _t_propio / _h_propio siempre actualizados.
#      Heartbeat muestra los valores reales del momento.
#
#  [4] HEARTBEAT CON ESTADO
#      Cada 5s: parpadeo 80ms mostrando:
#        • T y H actuales
#        • Modo actual: "→ WiFi" / "← MESH" / "IDLE"
#        • Nodos detectados
#      Cada 10 min: pantalla completa 5s.
#
#  Radio: idéntico a v12.2 (arranque_seguro probado).
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

CLIENT_ID     = "MASTER_TTGO_R"
TOPIC_SUB     = b"comandos/mesh"
TOPIC_PUB     = b"datos/sensores"
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

# [1] Tiempos reducidos para respuesta ≤ 5s al servidor
T_WIFI_CONNECT = 4_000    # Timeout conexión WiFi
T_MESH_LISTEN  = 4_000    # Ventana escucha ESP-NOW
T_HEARTBEAT    = 5_000    # Parpadeo de vida
T_10MIN        = 600_000  # Pantalla completa cada 10 min
DISPLAY_10MIN  = 5_000    # [4] Pantalla completa 5s (no 15s)
DISPLAY_BTN    = 10_000   # Pantalla en botón 10s
BROADCAST_N    = 3

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
#  ESTADO
# ───────────────────────────────────────────────
cola_subida   = []
cola_bajada   = []
_nodos_vistos = []
_t_propio     = "--"
_h_propio     = "--"
_wif_ok       = None
_mq_ok        = None
_svr_ok       = None
_modo_actual  = "INIT"   # [4] texto del estado actual para heartbeat

# ───────────────────────────────────────────────
#  RADIO
# ───────────────────────────────────────────────
_sta = None

def arranque_seguro():
    global _sta
    print("[RADIO] Limpiando estado previo...")
    try:
        en_tmp = espnow.ESPNow()
        en_tmp.active(False)
        del en_tmp
        gc.collect()
        utime.sleep_ms(300)
        print("[RADIO] ESP-NOW cerrado OK")
    except Exception as e:
        print("[RADIO] ESP-NOW cleanup:", e)
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
#  FLAGS IRQ
#  [2] La ISR enciende el backlight INMEDIATAMENTE.
#      Dar feedback visual en <1ms sin esperar al loop.
# ───────────────────────────────────────────────
_flag_server = False
_flag_mesh   = False

def _isr_server(pin):
    global _flag_server
    _flag_server = True
    backlight.value(1)   # feedback visual inmediato

def _isr_mesh(pin):
    global _flag_mesh
    _flag_mesh = True
    backlight.value(1)   # feedback visual inmediato

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

def heartbeat(modo=""):
    """
    [4] Parpadeo 80ms con datos actuales.
    Layout (240×135):
      Master v13      14:32:05
      T: 23C   H: 65%        ← siempre frescos
      → WiFi OK               ← modo actual
      S01 S02 S03            ← nodos detectados
    """
    hr, mn, seg = utime.localtime()[3:6]
    hora  = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    th    = "T:{}°C  H:{}%".format(_t_propio, _h_propio)
    nodos = " ".join(_nodos_vistos[-4:]) if _nodos_vistos else "sin nodos"
    m     = modo or _modo_actual

    tft.fill(NEGRO)
    tft.write(font_sm, "Master v13",  4,   4,  VERDE)
    tft.write(font_sm, hora,       148, 4,  GRIS)
    tft.write(font_sm, th,         4,   26, AMARILLO)
    tft.write(font_sm, m[:26],     4,   50, CYAN)
    tft.write(font_sm, nodos[:30], 4,   74, GRIS)

    backlight.value(1)
    utime.sleep_ms(80)
    backlight.value(0)

def pantalla_completa():
    """Pantalla grande T/H + estado. Backlight ON — caller la apaga."""
    ws, wc   = _est(_wif_ok)
    ms, mc   = _est(_mq_ok)
    svs, svc = _est(_svr_ok)
    hr, mn, seg = utime.localtime()[3:6]
    hora  = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    nodos = " ".join(_nodos_vistos[-5:]) if _nodos_vistos else "sin nodos"

    tft.fill(NEGRO)
    tft.write(font_sm, "MASTER",               4,   2,  VERDE)
    tft.write(font_sm, hora,                       160, 2,  CYAN)
    tft.write(font_md, "T: {}°C".format(_t_propio),  4,  22, AMARILLO)
    tft.write(font_md, "H: {}%".format(_h_propio),  4,  52, CYAN)
    tft.write(font_sm, nodos[:30],                  4,  84, GRIS)
    tft.write(font_sm, "WiFi:", 4,   108, BLANCO);  tft.write(font_sm, ws,  52,  108, wc)
    tft.write(font_sm, "MQTT:", 85,  108, BLANCO);  tft.write(font_sm, ms,  133, 108, mc)
    tft.write(font_sm, "Svr:",  166, 108, BLANCO);  tft.write(font_sm, svs, 200, 108, svc)
    backlight.value(1)

def _btn_texto(accion):
    """[2] Texto de acción que aparece ~100ms tras presionar el botón."""
    tft.fill(NEGRO)
    tft.write(font_sm, "PIF MASTER", 4,  4,  VERDE)
    tft.write(font_md, accion,       4,  30, AMARILLO)
    tft.write(font_sm, "ejecutando...", 4, 70, GRIS)
    # backlight ya está ON desde la ISR

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF MASTER",   4,  8,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     4,  44, CYAN)
    tft.write(font_sm, "v13.0",        4,  68, AMARILLO)
    tft.write(font_sm, "Iniciando...", 4,  92, BLANCO)
    backlight.value(1)
    utime.sleep_ms(2000)
    backlight.value(0)

# ───────────────────────────────────────────────
#  SENSORES
#  [3] Se llama al inicio de cada ciclo para mantener
#      _t_propio / _h_propio siempre actualizados.
# ───────────────────────────────────────────────
def medir_propio():
    """Lee DHT11 y actualiza globals. Siempre fresco."""
    global _t_propio, _h_propio
    for _ in range(3):
        t, h = hw.leer_dht()
        if t != "Error":
            _t_propio = t
            _h_propio = h
            return t, h
        utime.sleep_ms(800)
    return "Err", "Err"

def _payload_csv(hora, nodo, payload):
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
#  VENTANA WIFI — [1] T_WIFI_CONNECT reducido a 4s
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
    global _wif_ok, _mq_ok, _svr_ok, _modo_actual
    _wif_ok = _mq_ok = _svr_ok = False
    _modo_actual = "→ WiFi conectando..."

    if not _sta.isconnected():
        _sta.connect(WIFI_SSID, WIFI_PASS)
        fin = utime.ticks_add(utime.ticks_ms(), T_WIFI_CONNECT)
        while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
            if _sta.isconnected(): break
            utime.sleep_ms(200)

    if not _sta.isconnected():
        _modo_actual = "→ WiFi SIN CONEXION"
        print("[WIFI] Sin conexion")
        return

    _wif_ok = True
    _modo_actual = "→ WiFi OK  mandando datos"
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
        _modo_actual = "→ WiFi OK  env:{}".format(enviados)
        print("[MQTT OK] enviados:", enviados)

    except Exception as e:
        _modo_actual = "→ WiFi ERR MQTT"
        print("[MQTT ERR]", e)
    finally:
        if client:
            try: client.disconnect()
            except: pass
            del client
        gc.collect()

    try: _sta.disconnect()
    except: pass
    utime.sleep_ms(200)

# ───────────────────────────────────────────────
#  VENTANA MESH — [1] T_MESH_LISTEN reducido a 4s
# ───────────────────────────────────────────────
def ventana_mesh(cmd="REQ:ALL", target="ALL"):
    global _nodos_vistos, _modo_actual
    _modo_actual = "← MESH enviando WAVE..."

    _sta.config(channel=6)
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    if cola_bajada:
        onda = cola_bajada.pop(0)
        print("[MESH TX custom]", onda[:60])
    else:
        onda = json.dumps({
            "type": "WAVE", "cmd": cmd,
            "from": CLIENT_ID, "target": target, "ttl": 6
        })
        print("[MESH TX default] cmd:", cmd)

    for _ in range(BROADCAST_N):
        try: en.send(BROADCAST_MAC, onda)
        except Exception as e: print("[TX ERR]", e)
        utime.sleep_ms(150)
    del onda

    _modo_actual = "← MESH escuchando nodos..."
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
                _modo_actual = "← MESH nodo: " + nodo
            del data, txt
        except Exception as e:
            print("[RX ERR]", e)

    _modo_actual = "← MESH OK  nodos:{}".format(recibidos)
    print("[MESH OK] nodos:", recibidos)

    en.active(False)
    del en
    gc.collect()
    return recibidos

# ───────────────────────────────────────────────
#  LOOP PRINCIPAL
# ───────────────────────────────────────────────
def loop():
    global _flag_server, _flag_mesh, _modo_actual

    t_ultimo_hb    = utime.ticks_ms()
    t_ultimo_10min = utime.ticks_ms() - T_10MIN   # forzar pantalla al arrancar

    # Primera medición antes de entrar al bucle
    medir_propio()

    while True:
        gc.collect()

        # ── [3] Medir sensor al inicio de cada iteración ──
        medir_propio()

        # ── [2] Botón servidor ─────────────────────────────
        if _flag_server:
            _flag_server = False
            # backlight ya está ON desde la ISR
            _btn_texto("SERVIDOR")
            utime.sleep_ms(100)   # tiempo para que se vea el texto

            hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
            cola_subida.append("{},{},T:{} H:{},sensor".format(
                hora, CLIENT_ID, _t_propio, _h_propio))

            ventana_wifi()
            pantalla_completa()
            utime.sleep_ms(DISPLAY_BTN)
            backlight.value(0)
            t_ultimo_10min = t_ultimo_hb = utime.ticks_ms()
            continue

        # ── [2] Botón mesh ──────────────────────────────────
        if _flag_mesh:
            _flag_mesh = False
            # backlight ya está ON desde la ISR
            _btn_texto("MESH")
            utime.sleep_ms(100)

            ventana_mesh(cmd="REQ:ALL", target="ALL")
            pantalla_completa()
            utime.sleep_ms(DISPLAY_BTN)
            backlight.value(0)
            t_ultimo_hb = utime.ticks_ms()
            continue

        # ── Ciclo normal: wifi → mesh ───────────────────────
        ventana_wifi()
        ventana_mesh()
        t_ultimo_hb = utime.ticks_ms()

        # ── [4] Pantalla cada 10 min (5s) ──────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_10min) >= T_10MIN:
            pantalla_completa()
            utime.sleep_ms(DISPLAY_10MIN)
            backlight.value(0)
            t_ultimo_10min = t_ultimo_hb = utime.ticks_ms()

        # ── [4] Heartbeat cada 5s ───────────────────────────
        if utime.ticks_diff(utime.ticks_ms(), t_ultimo_hb) >= T_HEARTBEAT:
            heartbeat()
            t_ultimo_hb = utime.ticks_ms()

        print("[RAM]", gc.mem_free(),
              "| T:{} H:{}".format(_t_propio, _h_propio),
              "| sub:", len(cola_subida),
              "| nodos:", len(_nodos_vistos))

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
gc.collect()

if not arranque_seguro():
    tft.fill(NEGRO)
    tft.write(font_md, "RADIO ERR",     4, 8,  ROJO)
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
        machine.reset()
