# ============================================================
#  PIF_NODE v12.1 — Plantilla Universal / LAB-ARTE
#
#  ★ ÚNICO PARÁMETRO A CAMBIAR POR DISPOSITIVO ★
#    NODE_ID = "SLAVE_XX"
#
#  Cambios vs v12.0:
#    [NUEVO 1] Display apagado por defecto. Se prende SOLO en:
#                (a) WAVE recibida cuyo target soy yo (o ALL)
#                (b) botón derecho (medición forzada)
#                (c) entrar en modo alerta
#              Auto-apaga a los T_DISPLAY_ON_MS (8000ms).
#              El heartbeat ya NO enciende la pantalla.
#    [NUEVO 2] Modo alerta con umbrales por sensor.
#              Histéresis para evitar oscilación entrada/salida.
#              FB periódico cada ALERTA_PERIODO_MS + jitter aleatorio
#              del 30% para evitar tormentas sincronizadas.
#              Campo "alert":true en el FB para que el servidor pueda
#              distinguir mediciones normales de alertas.
#
#  Cambios vs v11.9:
#    [PREV CRÍTICO] Sin ciclo abrir/cerrar de ESP-NOW en modo siempre-
#                   despierto (evita ESP_ERR_ESPNOW_NO_MEM).
#
#  Cambios vs v11.8:
#    [PREV] Re-escaneo automático de canales in-situ.
#    [PREV] Pulsación larga del botón izquierdo cambia el modo.
# ============================================================

import gc, network, espnow, machine, utime, json
from machine import Pin, lightsleep, I2C, ADC, RTC
import dht
import tft_config
import st7789py as st7789
import comfortaa_16 as font_sm
import comfortaa_24 as font_md

gc.collect()

# ───────────────────────────────────────────────
#  ★ CAMBIAR EN CADA DISPOSITIVO ★
# ───────────────────────────────────────────────
NODE_ID = "SLAVE_01"

# ───────────────────────────────────────────────
#  MODO DE OPERACIÓN
#  El modo se carga desde 'node_config.json' en flash. Si no existe el
#  archivo, se usa MODO_DEFAULT. Una pulsación larga (3s) del botón
#  izquierdo alterna el modo y reinicia el nodo.
#
#  True  → siempre despierto, latencia ~100ms, consumo ~30-60 mA.
#          Recomendado para los relays (Slave_1, Slave_4 del diagrama)
#          o si todos los slaves tienen alimentación USB.
#  False → lightsleep cíclico, latencia hasta SLEEP_MS, bajo consumo.
# ───────────────────────────────────────────────
MODO_DEFAULT = True
CONFIG_FILE  = 'node_config.json'

def cargar_modo():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        return bool(cfg.get('siempre_despierto', MODO_DEFAULT))
    except:
        return MODO_DEFAULT

def guardar_modo(siempre_despierto):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump({'siempre_despierto': bool(siempre_despierto)}, f)
        return True
    except Exception as e:
        print("[CFG ERR]", e)
        return False

MODO_SIEMPRE_DESPIERTO = cargar_modo()

# ───────────────────────────────────────────────
#  TIEMPOS
# ───────────────────────────────────────────────
SLEEP_MS         = 600_000
VENTANA_WAVE_MS  = 8_000
VENTANA_HIJOS_MS = 3_000
CANAL_SCAN_MS    = 2_000
CANAL_MISS_MAX   = 3
BUFFER_MAX       = 5
MAX_TTL          = 6
BROADCAST_MAC    = b'\xff\xff\xff\xff\xff\xff'
CANALES_SCAN     = [1, 6, 11]

# [NUEVO 1] Si pasan más de T_RESCAN_MS sin recibir WAVE en modo siempre-
# despierto, re-escanear canales. El master manda BEACON cada 60s, así
# que 90s sin nada significa que perdimos sincronía.
T_RESCAN_MS      = 90_000

# [NUEVO 2] Pulsación larga (ms) para cambiar de modo
T_LONG_PRESS_MS  = 3_000

# ───────────────────────────────────────────────
#  [NUEVO 1] CONTROL DE DISPLAY
#  El display arranca apagado y solo se prende en eventos.
# ───────────────────────────────────────────────
T_DISPLAY_ON_MS  = 8_000   # cuánto dura el display prendido tras un evento

# ───────────────────────────────────────────────
#  [NUEVO 2] MODO ALERTA — umbrales con histéresis
#
#  Cómo leerlo:
#    "alto"      → si la lectura SUPERA este valor, entrar en alerta.
#    "sale_alto" → para SALIR de la alerta, la lectura debe bajar de aquí.
#                  El gap entre "alto" y "sale_alto" es la histéresis.
#    Análogo para "bajo" y "sale_bajo".
#    Pon None en cualquier umbral que no aplique.
#
#  En alerta el nodo manda FB cada ALERTA_PERIODO_MS ± ALERTA_JITTER_PCT%
#  El jitter evita que múltiples nodos en alerta sincronicen sus envíos
#  y se colisionen en el aire.
# ───────────────────────────────────────────────
UMBRALES = {
    "Temp":  {"alto": 30.0, "sale_alto": 28.0, "bajo": 5.0,  "sale_bajo": 8.0},
    "Hum":   {"alto": 85.0, "sale_alto": 80.0, "bajo": 20.0, "sale_bajo": 25.0},
    "MQ135": {"alto": 2500, "sale_alto": 2000, "bajo": None, "sale_bajo": None},
}
ALERTA_PERIODO_MS = 10_000   # base entre FBs en alerta
ALERTA_JITTER_PCT = 30       # ±30% jitter aleatorio

# Dedup global de WAVEs ya procesadas (por mid)
DEDUP_TTL_MS     = 30_000   # un mid se "olvida" después de 30s
_waves_vistas    = {}       # {mid: ticks_ms}

# ───────────────────────────────────────────────
#  PINES
# ───────────────────────────────────────────────
PIN_DHT11 = 15
PIN_MQ135 = 34
PIN_SDA   = 21
PIN_SCL   = 22
MPU_ADDR  = 0x68

# ───────────────────────────────────────────────
#  HARDWARE
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
backlight = Pin(4, Pin.OUT)
backlight.value(0)

sensor_dht = dht.DHT11(Pin(PIN_DHT11, Pin.IN))
sensor_mq  = ADC(Pin(PIN_MQ135))
sensor_mq.atten(ADC.ATTN_11DB)
i2c        = I2C(0, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=400_000)

btn_izq = Pin(0,  Pin.IN, Pin.PULL_UP)
btn_der = Pin(35, Pin.IN, Pin.PULL_UP)

# Wake-on-button solo aplica en modo lightsleep
if not MODO_SIEMPRE_DESPIERTO:
    import esp32 as _esp32
    _esp32.wake_on_ext0(pin=btn_izq, level=_esp32.WAKEUP_ALL_LOW)

rtc = RTC()
_hora_sincronizada = False   # se vuelve True al recibir el primer "ts" del master

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
AZUL     = st7789.color565(80, 160, 255)

# ───────────────────────────────────────────────
#  ESTADO DEL NODO
# ───────────────────────────────────────────────
_t_cache      = "--"
_h_cache      = "--"
_mq_cache     = "--"
_conectado    = False
_ultimo_padre = "MASTER_TTGO_GATEWAY"
_rol          = "?"
_canal_actual = 1
_ciclos_sin_wave = 0
_buffer       = []
_sensor_tipo  = "DHT11"

# [NUEVO 1] Display gestionado: arranca apagado, se prende solo en eventos
_display_apaga_en = 0

# [NUEVO 2] Estado de alerta (por tipo de sensor: 'Temp', 'Hum', 'MQ135')
# Valores posibles para cada sensor: None (normal), 'alto', 'bajo'
_alertas_activas    = {}
_proxima_alerta_tx  = 0   # ticks_ms a partir del cual hay que mandar siguiente FB de alerta

# ───────────────────────────────────────────────
#  [NUEVO 1] HELPERS DE DISPLAY
# ───────────────────────────────────────────────
def prender_display():
    """Prende backlight y arma timer de auto-apagado."""
    global _display_apaga_en
    backlight.value(1)
    _display_apaga_en = utime.ticks_add(utime.ticks_ms(), T_DISPLAY_ON_MS)

def poll_display():
    """Llamar regularmente desde el loop: apaga el display si venció el timer."""
    if _display_apaga_en and utime.ticks_diff(_display_apaga_en, utime.ticks_ms()) < 0:
        backlight.value(0)

def display_esta_prendido():
    if not _display_apaga_en: return False
    return utime.ticks_diff(_display_apaga_en, utime.ticks_ms()) > 0

# ───────────────────────────────────────────────
#  [NUEVO 2] HELPERS DE ALERTA
# ───────────────────────────────────────────────
def _val_num(v):
    """Convierte 'v' a número si es posible, devuelve None si no."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except:
        return None

def evaluar_alertas(mediciones):
    """
    Recorre mediciones y actualiza _alertas_activas con histéresis.
    Devuelve True si HAY al menos una alerta activa después de evaluar.
    """
    global _alertas_activas
    for m in mediciones:
        t = m.get("t")
        v = _val_num(m.get("v"))
        if v is None or t not in UMBRALES:
            continue
        u = UMBRALES[t]
        estado_actual = _alertas_activas.get(t)

        # Entrada en alerta alto
        if u["alto"] is not None and v > u["alto"] and estado_actual != "alto":
            _alertas_activas[t] = "alto"
            print("[ALERTA] {} = {} > {} → modo alto".format(t, v, u["alto"]))
        # Salida de alerta alto (histéresis)
        elif estado_actual == "alto" and u["sale_alto"] is not None and v < u["sale_alto"]:
            del _alertas_activas[t]
            print("[ALERTA] {} = {} < {} → salida".format(t, v, u["sale_alto"]))
        # Entrada en alerta bajo
        elif u["bajo"] is not None and v < u["bajo"] and estado_actual != "bajo":
            _alertas_activas[t] = "bajo"
            print("[ALERTA] {} = {} < {} → modo bajo".format(t, v, u["bajo"]))
        # Salida de alerta bajo (histéresis)
        elif estado_actual == "bajo" and u["sale_bajo"] is not None and v > u["sale_bajo"]:
            del _alertas_activas[t]
            print("[ALERTA] {} = {} > {} → salida".format(t, v, u["sale_bajo"]))
    return len(_alertas_activas) > 0

def _proximo_intervalo_alerta():
    """Devuelve ALERTA_PERIODO_MS aleatorizado ±ALERTA_JITTER_PCT%."""
    import urandom
    base = ALERTA_PERIODO_MS
    delta = base * ALERTA_JITTER_PCT // 100
    # urandom.getrandbits no es uniforme cómodo; usamos mod
    jitter = (urandom.getrandbits(16) % (2 * delta + 1)) - delta
    return base + jitter

# ───────────────────────────────────────────────
#  AUTO-DETECCIÓN DE SENSOR
# ───────────────────────────────────────────────
def detectar_sensor():
    global _sensor_tipo
    print("[SENSOR] Detectando DHT11...")
    utime.sleep_ms(1000)
    for i in range(3):
        try:
            sensor_dht.measure()
            tv = sensor_dht.temperature()
            hv = sensor_dht.humidity()
            if tv is not None and hv is not None and not (tv == 0 and hv == 0):
                _sensor_tipo = "DHT11"
                print("[SENSOR] DHT11 OK — modo Temperatura/Humedad")
                return
        except Exception as e:
            print("[SENSOR] DHT11 intento {} fallo: {}".format(i+1, e))
        utime.sleep_ms(800)
    _sensor_tipo = "MQ135"
    print("[SENSOR] DHT11 no responde — modo MQ135 (calidad aire)")

# ───────────────────────────────────────────────
#  RADIO
# ───────────────────────────────────────────────
_sta = network.WLAN(network.STA_IF)
_en_global = None   # En modo siempre-despierto, ESP-NOW vive aquí

def init_espnow(canal):
    gc.collect()
    if not _sta.active():
        _sta.active(True)
    _sta.config(channel=canal)
    utime.sleep_ms(150)
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)
    return en

def cerrar_espnow(en):
    try:    en.active(False)
    except: pass
    try:    del en
    except: pass
    if not MODO_SIEMPRE_DESPIERTO:
        try: _sta.active(False)
        except: pass
    gc.collect()

# ───────────────────────────────────────────────
#  ESCANEO DE CANAL
# ───────────────────────────────────────────────
def escanear_canal():
    global _canal_actual
    print("[SCAN] Buscando canal del Master...")
    _barra_status("Buscando Master...", AMARILLO)

    for ch in CANALES_SCAN:
        _sta.active(True)
        _sta.config(channel=ch)
        utime.sleep_ms(100)
        en = espnow.ESPNow()
        en.active(True)
        en.add_peer(BROADCAST_MAC)

        fin = utime.ticks_add(utime.ticks_ms(), CANAL_SCAN_MS)
        encontrado = False
        while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
            try: host, msg = en.recv(50)
            except: continue
            if not msg: continue
            try:
                data = json.loads(msg.decode())
                if data.get("type") == "WAVE":
                    ch_master = data.get("ch", ch)
                    _canal_actual = ch_master
                    encontrado = True
                    # Aprovechar para sincronizar reloj
                    sincronizar_rtc_desde(data.get("ts"))
                    print("[SCAN] Master en canal", _canal_actual)
                    del data
                    break
                del data
            except: pass

        en.active(False)
        del en
        _sta.active(False)
        gc.collect()

        if encontrado:
            _barra_status("Canal: {}".format(_canal_actual), VERDE)
            utime.sleep_ms(300)
            return True

    print("[SCAN] No encontrado, usando ch:", _canal_actual)
    return False

# ───────────────────────────────────────────────
#  ESCANEO IN-SITU (modo siempre-despierto)
#  No abre ni cierra ESP-NOW. Solo cambia el canal del WiFi STA y
#  drena el buffer del 'en' que ya está activo. Esto evita el bug
#  ESP_ERR_ESPNOW_NO_MEM que ocurre al ciclar active(False)/active(True)
#  en MicroPython sobre IDF v5.x.
# ───────────────────────────────────────────────
def escanear_canal_in_situ(en):
    global _canal_actual
    print("[SCAN-IS] Buscando canal del Master (in-situ)...")
    _barra_status("Buscando Master...", AMARILLO)

    canal_original = _canal_actual

    for ch in CANALES_SCAN:
        try:
            _sta.config(channel=ch)
        except Exception as e:
            print("[SCAN-IS] config ch:{} err:{}".format(ch, e))
            continue
        utime.sleep_ms(150)
        print("[SCAN-IS] Probando ch:", ch)

        fin = utime.ticks_add(utime.ticks_ms(), CANAL_SCAN_MS)
        while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
            try: host, msg = en.recv(50)
            except: continue
            if not msg: continue
            try:
                data = json.loads(msg.decode())
                if data.get("type") == "WAVE":
                    ch_master = data.get("ch", ch)
                    if ch_master != ch:
                        try: _sta.config(channel=ch_master)
                        except: pass
                    _canal_actual = ch_master
                    sincronizar_rtc_desde(data.get("ts"))
                    print("[SCAN-IS] Master en canal", _canal_actual)
                    _barra_status("Canal: {}".format(_canal_actual), VERDE)
                    utime.sleep_ms(300)
                    return True
                del data
            except: pass

        gc.collect()

    # No encontrado: restaurar canal original (probablemente seguía OK)
    try: _sta.config(channel=canal_original)
    except: pass
    _canal_actual = canal_original
    print("[SCAN-IS] No encontrado, mantengo ch:", _canal_actual)
    return False

# ───────────────────────────────────────────────
#  SINCRONIZACIÓN DE HORA
#  El master envía "ts":"YYYY-MM-DD HH:MM:SS" en cada WAVE.
#  Ajustamos el RTC del ESP32 para que las marcas de tiempo
#  del buffer sean hora real.
# ───────────────────────────────────────────────
def sincronizar_rtc_desde(ts_str):
    global _hora_sincronizada
    if not ts_str or not isinstance(ts_str, str):
        return False
    try:
        # Formato esperado: "2026-05-06 14:25:30"
        fecha, hora = ts_str.split(" ")
        a, m, d  = [int(x) for x in fecha.split("-")]
        hh, mm, ss = [int(x) for x in hora.split(":")]
        # RTC.datetime: (año, mes, día, día_semana, h, m, s, microseg)
        rtc.datetime((a, m, d, 0, hh, mm, ss, 0))
        _hora_sincronizada = True
        return True
    except Exception as e:
        print("[RTC ERR]", e)
        return False

# ───────────────────────────────────────────────
#  DEDUP GLOBAL DE WAVES
# ───────────────────────────────────────────────
def wave_ya_vista(mid):
    if mid is None:
        return False
    # Limpiar viejos
    ahora = utime.ticks_ms()
    a_borrar = []
    for k, v in _waves_vistas.items():
        if utime.ticks_diff(ahora, v) > DEDUP_TTL_MS:
            a_borrar.append(k)
    for k in a_borrar:
        del _waves_vistas[k]
    if mid in _waves_vistas:
        return True
    _waves_vistas[mid] = ahora
    return False

# ───────────────────────────────────────────────
#  MPU6050
# ───────────────────────────────────────────────
def _s16(v):
    return v if v < 32768 else v - 65536

def mpu_init():
    try:
        i2c.writeto_mem(MPU_ADDR, 0x6B, b'\x00')
        utime.sleep_ms(80)
    except: pass

def mpu_leer():
    try:
        raw = i2c.readfrom_mem(MPU_ADDR, 0x3B, 6)
        ax  = round(_s16(raw[0] << 8 | raw[1]) / 16384.0, 2)
        ay  = round(_s16(raw[2] << 8 | raw[3]) / 16384.0, 2)
        az  = round(_s16(raw[4] << 8 | raw[5]) / 16384.0, 2)
        return ax, ay, az
    except:
        return None, None, None

# ───────────────────────────────────────────────
#  SENSORES
# ───────────────────────────────────────────────
def leer_sensores():
    global _t_cache, _h_cache, _mq_cache
    med = []
    t, h, mq = "--", "--", "--"

    if _sensor_tipo == "DHT11":
        t, h = "ERR", "ERR"
        utime.sleep_ms(500)
        for _ in range(3):
            try:
                sensor_dht.measure()
                tv = sensor_dht.temperature()
                hv = sensor_dht.humidity()
                if not (tv == 0 and hv == 0):
                    t, h = tv, hv
                    break
            except: pass
            utime.sleep_ms(300)
        _t_cache = t
        _h_cache = h
        med.append({"t": "Temp", "v": t})
        med.append({"t": "Hum",  "v": h})

    if _sensor_tipo == "MQ135":
        try:
            mq = sensor_mq.read()
        except:
            mq = "ERR"
        _mq_cache = mq
        med.append({"t": "MQ135", "v": mq})

    ax, ay, az = mpu_leer()
    if ax is not None:
        med.append({"t": "AccX", "v": ax})
        med.append({"t": "AccY", "v": ay})
        med.append({"t": "AccZ", "v": az})

    return med, (t, h, mq, ax, ay)

# ───────────────────────────────────────────────
#  DISPLAY
# ───────────────────────────────────────────────
W = tft.physical_height
H = tft.physical_width

def cx(font, txt):
    return max(0, (W - tft.write_width(font, txt)) // 2)

def _barra_status(msg, col=VERDE):
    tft.fill_rect(0, 108, W, 27, NEGRO)
    tft.fill_rect(4, 112, 8, 14, col)
    tft.write(font_sm, msg[:24], 16, 112, col)

def ui_nodo(t, h, mq, ax=None, ay=None, status="", status_col=VERDE):
    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    tft.fill(NEGRO)
    tft.write(font_sm, NODE_ID, 4,   2, CYAN)
    tft.write(font_sm, hora,    160, 2, GRIS if not _hora_sincronizada else VERDE)

    if _sensor_tipo == "DHT11":
        t_col = ROJO if t == "ERR" else AMARILLO
        h_col = ROJO if h == "ERR" else CYAN
        tft.write(font_md, "T: {}C".format(t), 4, 22, t_col)
        tft.write(font_md, "H: {}%".format(h), 4, 52, h_col)
        extras = ""
        if ax is not None:
            extras = "Ax:{:.1f}  Ay:{:.1f}".format(ax, ay)
        if extras:
            tft.write(font_sm, extras[:30], 4, 84, BLANCO)
    else:
        mq_col = ROJO if mq == "ERR" else AMARILLO
        try:
            mqv = int(mq)
            if   mqv < 700:  cat, cat_col = "BUENA", VERDE
            elif mqv < 1500: cat, cat_col = "MODERADA", AMARILLO
            elif mqv < 2500: cat, cat_col = "MALA", st7789.color565(255, 140, 0)
            else:            cat, cat_col = "MUY MALA", ROJO
        except:
            cat, cat_col = "---", GRIS
        tft.write(font_sm, "CALIDAD AIRE", 4, 22, GRIS)
        tft.write(font_md, "{}".format(mq), 4, 42, mq_col)
        tft.write(font_md, cat,             4, 76, cat_col)

    tft.fill_rect(0, 108, W, 27, NEGRO)
    tft.fill_rect(4, 112, 8, 14, status_col)
    tft.write(font_sm, status[:24], 16, 112, status_col)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF NODE",  cx(font_md, "PIF NODE"),  4,  VERDE)
    tft.write(font_sm, "LAB-ARTE",  cx(font_sm, "LAB-ARTE"),  44, CYAN)
    tft.write(font_sm, NODE_ID,     cx(font_sm, NODE_ID),     68, AMARILLO)
    modo = "SIEMPRE ON" if MODO_SIEMPRE_DESPIERTO else "v12.1 sleep"
    tft.write(font_sm, modo,        cx(font_sm, modo),        92, GRIS)
    prender_display()
    utime.sleep_ms(2000)

def ui_modo_detectado():
    tft.fill(NEGRO)
    tft.write(font_sm, NODE_ID, cx(font_sm, NODE_ID), 20, CYAN)
    if _sensor_tipo == "DHT11":
        tft.write(font_md, "Modo: T/H",     cx(font_md, "Modo: T/H"),     50, VERDE)
        tft.write(font_sm, "DHT11 detectado", cx(font_sm, "DHT11 detectado"), 88, GRIS)
    else:
        tft.write(font_md, "Modo: AIRE",   cx(font_md, "Modo: AIRE"),   50, AMARILLO)
        tft.write(font_sm, "MQ135 activo",  cx(font_sm, "MQ135 activo"),  88, GRIS)
    prender_display()
    utime.sleep_ms(1500)

def ui_sleep_screen():
    tft.fill(NEGRO)
    tft.write(font_sm, NODE_ID,      cx(font_sm, NODE_ID),      40, CYAN)
    con_txt = "conectado" if _conectado else "sin senal"
    con_col = VERDE if _conectado else GRIS
    tft.write(font_sm, con_txt,      cx(font_sm, con_txt),      66, con_col)
    estado = "escuchando..." if MODO_SIEMPRE_DESPIERTO else "sleeping..."
    tft.write(font_sm, estado, cx(font_sm, estado), 92, GRIS)
    backlight.value(0)

# ───────────────────────────────────────────────
#  BUFFER LOCAL
# ───────────────────────────────────────────────
def _ts_actual():
    if _hora_sincronizada:
        lt = utime.localtime()
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            lt[0], lt[1], lt[2], lt[3], lt[4], lt[5])
    hr, mn, seg = utime.localtime()[3:6]
    return "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)

def agregar_al_buffer(mediciones):
    ts = _ts_actual()
    _buffer.append({"ts": ts, "pl": mediciones})
    while len(_buffer) > BUFFER_MAX:
        _buffer.pop(0)

def payload_completo(mediciones_fresh):
    combined = []
    for entry in _buffer:
        for m in entry["pl"]:
            combined.append({
                "t"  : m["t"],
                "v"  : m["v"],
                "ts" : entry["ts"]
            })
    for m in mediciones_fresh:
        combined.append({"t": m["t"], "v": m["v"]})
    _buffer.clear()
    return combined

# ───────────────────────────────────────────────
#  ESP-NOW — envío y relay
# ───────────────────────────────────────────────
def enviar_fb(en, payload, parent_id, mid_origen=None):
    pkt_dict = {
        "type": "FB",
        "id"  : NODE_ID,
        "par" : parent_id,
        "pl"  : payload
    }
    if mid_origen is not None:
        pkt_dict["mid"] = mid_origen
    # [NUEVO 2] marcar el FB como alerta si tenemos alertas activas
    if _alertas_activas:
        pkt_dict["alert"] = True
        pkt_dict["a_t"]   = list(_alertas_activas.keys())   # tipos en alerta
    pkt = json.dumps(pkt_dict)
    if len(pkt) > 248:
        pkt_dict["pl"] = payload[:3]
        pkt = json.dumps(pkt_dict)
    try:
        ok = en.send(BROADCAST_MAC, pkt)
        utime.sleep_ms(80)
        marca = "OK" if ok else "FALLO"
        flag = " ALERT" if _alertas_activas else ""
        print("[>>> FB TX] {} → {}  mid:{}  bytes:{}  hw:{}{}".format(
            NODE_ID, parent_id, mid_origen, len(pkt), marca, flag))
        return ok
    except Exception as e:
        print("[FB ERR]", e)
        utime.sleep_ms(100)
        return False

def relay_fb_hijo(en, raw_str):
    try:
        data = json.loads(raw_str)
        via  = data.get("via", [])
        if NODE_ID in via:
            del data; return False
        via.append(NODE_ID)
        data["via"] = via
        relay = json.dumps(data)
        if len(relay) < 248:
            ok = en.send(BROADCAST_MAC, relay)
            utime.sleep_ms(80)
            marca = "OK" if ok else "FALLO"
            print("[RELAY] ← hijo:{}  hw:{}".format(data.get("id", "?"), marca))
        del data, relay
        return True
    except Exception as e:
        print("[RELAY ERR]", e)
        utime.sleep_ms(100)
        return False

def relay_wave(en, data, ttl_actual):
    """Propaga un WAVE con ttl-1, conservando mid y demás campos."""
    if ttl_actual <= 1:
        return False
    nuevo = {
        "type"  : "WAVE",
        "cmd"   : data.get("cmd", "REQ:ALL"),
        "from"  : NODE_ID,
        "target": data.get("target", "ALL"),
        "ttl"   : ttl_actual - 1,
        "ch"    : _canal_actual,
    }
    if "mid" in data: nuevo["mid"] = data["mid"]
    if "ts"  in data: nuevo["ts"]  = data["ts"]
    pkt = json.dumps(nuevo)
    try:
        ok1 = en.send(BROADCAST_MAC, pkt); utime.sleep_ms(150)
        ok2 = en.send(BROADCAST_MAC, pkt); utime.sleep_ms(80)
        print("[>>> WAVE PROP] mid:{}  ttl:{}  hw1:{}  hw2:{}".format(
            data.get("mid"), ttl_actual - 1, "OK" if ok1 else "FALLO",
            "OK" if ok2 else "FALLO"))
        return ok1 or ok2
    except Exception as e:
        print("[WAVE PROP ERR]", e)
        return False

# ───────────────────────────────────────────────
#  MANEJO DE WAVE — común a ambos modos
#  Devuelve True si la WAVE era para nosotros y se debe responder.
# ───────────────────────────────────────────────
def manejar_wave(en, data):
    global _conectado, _ultimo_padre, _ciclos_sin_wave, _canal_actual
    mid = data.get("mid")
    if wave_ya_vista(mid):
        print("[DEDUP WAVE] mid:{} ya visto, ignoro".format(mid))
        return False, None, None

    ttl    = data.get("ttl", MAX_TTL)
    target = data.get("target", "ALL")
    parent = data.get("from", "MASTER_TTGO_GATEWAY")
    cmd    = data.get("cmd", "REQ:ALL")

    # Sincronizar reloj con el master
    sincronizar_rtc_desde(data.get("ts"))

    # Adoptar canal si difiere
    ch_master = data.get("ch", _canal_actual)
    if ch_master != _canal_actual:
        _canal_actual = ch_master
        print("[CH] Actualizado a", _canal_actual)

    _conectado    = True
    _ultimo_padre = parent
    _ciclos_sin_wave = 0
    print("[<<< WAVE RX] de:{}  cmd:{}  target:{}  ttl:{}  mid:{}".format(
        parent, cmd, target, ttl, mid))

    # Propagar a hijos si el TTL aún tiene aire
    if ttl > 1:
        relay_wave(en, data, ttl)

    # Decidir si soy el target
    debe_responder = (target == "ALL" or target == NODE_ID)
    return debe_responder, parent, mid

# ───────────────────────────────────────────────
#  CAMBIO DE MODO POR BOTÓN LARGO
# ───────────────────────────────────────────────
def cambiar_modo_y_reiniciar():
    """Pulsación larga del botón izquierdo: alterna el modo en flash y reinicia."""
    nuevo = not MODO_SIEMPRE_DESPIERTO
    prender_display()
    tft.fill(NEGRO)
    tft.write(font_sm, NODE_ID, cx(font_sm, NODE_ID), 4, CYAN)
    tft.write(font_md, "Cambio modo", cx(font_md, "Cambio modo"), 28, AMARILLO)
    txt_modo = "SIEMPRE ON" if nuevo else "SLEEP"
    tft.write(font_md, txt_modo, cx(font_md, txt_modo), 60, VERDE)
    tft.write(font_sm, "Reiniciando...", cx(font_sm, "Reiniciando..."), 100, GRIS)
    guardar_modo(nuevo)
    utime.sleep_ms(2000)
    machine.reset()

# ============================================================
#  MODO SIEMPRE-DESPIERTO
#  El radio nunca se apaga. ESP-NOW recibe en bucle no-bloqueante.
# ============================================================
def loop_siempre_despierto():
    global _en_global, _conectado, _ciclos_sin_wave

    print("[MODO] Siempre despierto activo")
    # IMPORTANTE: NO reinicializamos ESP-NOW aquí. _en_global ya está
    # vivo desde el bloque de arranque. Reabrirlo causa NO_MEM.
    en = _en_global
    if en is None:
        print("[FATAL] _en_global no inicializado")
        return

    fb_ya_relayados = {}   # {clave: ticks_ms} con TTL
    ultimo_heartbeat = utime.ticks_ms()
    ultima_medicion_auto = utime.ticks_ms()
    ultimo_wave_rx = utime.ticks_ms()        # [NUEVO 1] tracking re-escaneo
    btn_izq_press_start = None               # [NUEVO 2] tracking pulsación larga

    while True:
        try:
            gc.collect()

            # [NUEVO 2] Detección de pulsación larga del botón izquierdo
            # → cambiar modo y reiniciar
            if btn_izq.value() == 0:
                if btn_izq_press_start is None:
                    btn_izq_press_start = utime.ticks_ms()
                else:
                    held = utime.ticks_diff(utime.ticks_ms(), btn_izq_press_start)
                    if held > T_LONG_PRESS_MS:
                        cambiar_modo_y_reiniciar()
            else:
                btn_izq_press_start = None

            # Re-escaneo automático si llevamos mucho sin WAVE
            if utime.ticks_diff(utime.ticks_ms(), ultimo_wave_rx) > T_RESCAN_MS:
                print("[RESCAN] {}s sin WAVE, escaneando in-situ...".format(
                    T_RESCAN_MS // 1000))
                # Escaneo IN-SITU: NO cerramos ESP-NOW (eso causa NO_MEM).
                # NOTA [NUEVO 1]: el rescan NO prende display, es operación silenciosa.
                escanear_canal_in_situ(en)
                ultimo_wave_rx = utime.ticks_ms()
                continue

            # [NUEVO 1] poll de auto-apagado de display
            poll_display()

            # Botón derecho: medición forzada y FB inmediato
            if btn_der.value() == 0:
                prender_display()                          # [NUEVO 1]
                ui_nodo(_t_cache, _h_cache, _mq_cache,
                        status="Boton: midiendo...", status_col=AMARILLO)
                med, (t, h, mq, ax, ay) = leer_sensores()
                evaluar_alertas(med)                       # [NUEVO 2]
                ui_nodo(t, h, mq, ax, ay, status="Enviando FB...", status_col=AZUL)
                for i in range(3):
                    enviar_fb(en, med, _ultimo_padre, mid_origen=None)
                    utime.sleep_ms(300)
                _buffer.clear()
                ui_nodo(t, h, mq, ax, ay, status="Enviado", status_col=VERDE)
                prender_display()                          # refresca timer
                # Espera de antirebote
                while btn_der.value() == 0:
                    utime.sleep_ms(50)

            # Recibir paquete (timeout corto, no bloqueante)
            host = msg = None
            try:
                host, msg = en.recv(50)
            except:
                pass

            if msg:
                try:
                    txt = msg.decode()
                    data = json.loads(txt)
                    tipo = data.get("type")

                    if tipo == "WAVE":
                        ultimo_wave_rx = utime.ticks_ms()
                        debe_responder, parent, mid = manejar_wave(en, data)
                        if debe_responder:
                            # [NUEVO 1] WAVE-para-mí → prender display
                            prender_display()
                            ui_nodo(_t_cache, _h_cache, _mq_cache,
                                    status="WAVE recibida", status_col=VERDE)
                            med_fresh, (t2, h2, mq2, ax2, ay2) = leer_sensores()
                            evaluar_alertas(med_fresh)     # [NUEVO 2]
                            pl_envio = payload_completo(med_fresh)
                            enviar_fb(en, pl_envio, parent, mid_origen=mid)
                            ui_nodo(t2, h2, mq2, ax2, ay2,
                                    status="FB enviado", status_col=VERDE)
                            prender_display()              # refresca timer

                    elif tipo == "FB":
                        # Relay de FB ajeno
                        ajeno_id = data.get("id", "?")
                        ajeno_via = data.get("via", [])
                        ajeno_mid = data.get("mid", "")
                        if ajeno_id != NODE_ID and NODE_ID not in ajeno_via:
                            clave = "{}|{}".format(ajeno_id, ajeno_mid)
                            ahora = utime.ticks_ms()
                            # Limpieza ocasional del dedup de FBs
                            a_borrar = [k for k, v in fb_ya_relayados.items()
                                        if utime.ticks_diff(ahora, v) > DEDUP_TTL_MS]
                            for k in a_borrar: del fb_ya_relayados[k]
                            if clave not in fb_ya_relayados:
                                fb_ya_relayados[clave] = ahora
                                relay_fb_hijo(en, txt)

                    del data, txt
                except Exception as e:
                    print("[RX ERR]", e)

            # [NUEVO 1] Heartbeat SIN encender display — solo logging
            if utime.ticks_diff(utime.ticks_ms(), ultimo_heartbeat) > 30_000:
                ultimo_heartbeat = utime.ticks_ms()
                print("[HB] {} ch:{} alertas:{} buf:{} ram:{}".format(
                    NODE_ID, _canal_actual,
                    list(_alertas_activas.keys()) if _alertas_activas else "ok",
                    len(_buffer), gc.mem_free()))
                # Si el display ya está prendido por otra causa, lo refrescamos
                if display_esta_prendido():
                    ui_nodo(_t_cache, _h_cache, _mq_cache,
                            status="Activo ch:{}".format(_canal_actual),
                            status_col=GRIS)

            # Medición autónoma cada SLEEP_MS para alimentar el buffer + chequeo alerta
            if utime.ticks_diff(utime.ticks_ms(), ultima_medicion_auto) > SLEEP_MS:
                ultima_medicion_auto = utime.ticks_ms()
                med, (t, h, mq, ax, ay) = leer_sensores()
                hubo_alerta_antes = bool(_alertas_activas)
                hay_alerta = evaluar_alertas(med)          # [NUEVO 2]
                agregar_al_buffer(med)
                # Si recién entramos en alerta, encender display un momento
                if hay_alerta and not hubo_alerta_antes:
                    prender_display()
                    ui_nodo(t, h, mq, ax, ay,
                            status="ALERTA!", status_col=ROJO)
                print("[AUTO] Buffer:", len(_buffer),
                      "| alertas:", list(_alertas_activas.keys()) if _alertas_activas else "ninguna")

            # [NUEVO 2] FB periódico cuando hay alertas activas
            if _alertas_activas:
                if utime.ticks_diff(utime.ticks_ms(), _proxima_alerta_tx) >= 0:
                    # Medir fresco y mandar
                    med_a, (ta, ha, mqa, axa, aya) = leer_sensores()
                    # Re-evaluar (puede salir de alerta justo ahora con histéresis)
                    estaba_en_alerta = bool(_alertas_activas)
                    evaluar_alertas(med_a)
                    if _alertas_activas:
                        enviar_fb(en, med_a, _ultimo_padre, mid_origen=None)
                        intervalo = _proximo_intervalo_alerta()
                        _proxima_alerta_tx = utime.ticks_add(utime.ticks_ms(), intervalo)
                        print("[ALERTA TX] proximo en", intervalo, "ms")
                    elif estaba_en_alerta:
                        # Salimos de alerta — un FB final para informar al servidor
                        enviar_fb(en, med_a, _ultimo_padre, mid_origen=None)
                        print("[ALERTA] terminada, FB final enviado")
            else:
                # Si no hay alertas, reset del timer (queda armado en cuanto entre alguna)
                _proxima_alerta_tx = utime.ticks_ms()

            utime.sleep_ms(20)

        except Exception as e:
            print("[LOOP ERR]", e)
            utime.sleep_ms(500)

# ============================================================
#  MODO LIGHTSLEEP — comportamiento original v11.7
# ============================================================
def ciclo(forzar=False):
    global _conectado, _ultimo_padre, _rol, _ciclos_sin_wave, _canal_actual

    gc.collect()
    # [NUEVO 1] Display solo se prende si es ciclo forzado por botón
    if forzar:
        prender_display()
    mpu_init()

    if forzar:
        ui_nodo(_t_cache, _h_cache, _mq_cache,
                status="Midiendo...", status_col=AMARILLO)
        med, (t, h, mq, ax, ay) = leer_sensores()
        evaluar_alertas(med)                              # [NUEVO 2]
        ui_nodo(t, h, mq, ax, ay, status="Enviando FB...", status_col=AZUL)
        en = init_espnow(_canal_actual)
        for i in range(3):
            enviar_fb(en, med, _ultimo_padre, mid_origen=None)
            ui_nodo(t, h, mq, ax, ay,
                    status="Enviando {}/3".format(i+1), status_col=AZUL)
            utime.sleep_ms(400)
        _buffer.clear()
        ui_nodo(t, h, mq, ax, ay, status="Enviado (boton)", status_col=VERDE)
        utime.sleep_ms(1500)
        cerrar_espnow(en)
        ui_sleep_screen()
        lightsleep(SLEEP_MS)
        return

    # [NUEVO 1] Sin display para mediciones automáticas
    med, (t, h, mq, ax, ay) = leer_sensores()
    hubo_alerta_antes = bool(_alertas_activas)
    evaluar_alertas(med)                                  # [NUEVO 2]
    agregar_al_buffer(med)
    # Si recién entramos en alerta, prender display un momento
    if _alertas_activas and not hubo_alerta_antes:
        prender_display()
        ui_nodo(t, h, mq, ax, ay, status="ALERTA!", status_col=ROJO)
    elif display_esta_prendido():
        ui_nodo(t, h, mq, ax, ay,
                status="Buffer: {}".format(len(_buffer)), status_col=GRIS)

    en = init_espnow(_canal_actual)
    for i in range(2):
        enviar_fb(en, med, _ultimo_padre, mid_origen=None)
        utime.sleep_ms(300)
    print("[AUTO] FB x2 enviado | buffer:", len(_buffer), "| canal:", _canal_actual)

    _barra_status("Escuchando malla...", GRIS)
    wave_recibida_data = None
    fbs_relayados_paso3 = set()

    fin = utime.ticks_add(utime.ticks_ms(), VENTANA_WAVE_MS)
    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        try: host, msg = en.recv(30)
        except: continue
        if not msg: continue
        try:
            txt  = msg.decode()
            data = json.loads(txt)
            tipo_msg = data.get("type")

            if tipo_msg == "WAVE":
                if not wave_ya_vista(data.get("mid")):
                    wave_recibida_data = data
                    sincronizar_rtc_desde(data.get("ts"))
                del data; break

            elif tipo_msg == "FB":
                ajeno_id = data.get("id", "?")
                ajeno_via = data.get("via", [])
                if ajeno_id == NODE_ID:
                    del data, txt; continue
                if NODE_ID in ajeno_via:
                    del data, txt; continue
                clave = ajeno_id + "|" + str(data.get("mid", ""))
                if clave in fbs_relayados_paso3:
                    del data, txt; continue
                fbs_relayados_paso3.add(clave)
                relay_fb_hijo(en, txt)
                print("[RELAY auto] FB de", ajeno_id, "hacia Master")
            del data
        except: pass

    if wave_recibida_data is None:
        _ciclos_sin_wave += 1
        _conectado = False
        print("[NO WAVE] ciclos sin wave:", _ciclos_sin_wave)
        cerrar_espnow(en)
        if _ciclos_sin_wave >= CANAL_MISS_MAX:
            # [NUEVO 1] rescan silencioso, sin prender display
            escanear_canal()
            _ciclos_sin_wave = 0
        ui_sleep_screen()
        lightsleep(SLEEP_MS)
        return

    debe_responder, parent_id, wave_mid = manejar_wave(en, wave_recibida_data)

    # [NUEVO 1] WAVE-para-mí → prender display
    if debe_responder:
        prender_display()
        ui_nodo(t, h, mq, ax, ay,
                status="WAVE recibida", status_col=VERDE)
    med_fresh, (t2, h2, mq2, ax2, ay2) = leer_sensores()
    evaluar_alertas(med_fresh)                            # [NUEVO 2]
    if debe_responder:
        ui_nodo(t2, h2, mq2, ax2, ay2,
                status="Conectado: " + parent_id[:14], status_col=VERDE)
    utime.sleep_ms(300)

    gc.collect()

    _barra_status("Esperando FBs...", CYAN)
    hijos_detectados = []
    fb_ya_relayados  = set()

    fin_hijos = utime.ticks_add(utime.ticks_ms(), VENTANA_HIJOS_MS)
    while utime.ticks_diff(fin_hijos, utime.ticks_ms()) > 0:
        try: host, msg = en.recv(20)
        except: continue
        if not msg: continue
        try:
            txt  = msg.decode()
            data = json.loads(txt)
            if data.get("type") == "FB":
                hijo_id  = data.get("id", "?")
                hijo_par = data.get("par", "")
                via      = data.get("via", [])
                if hijo_id == NODE_ID:
                    del data, txt; continue
                if NODE_ID in via:
                    del data, txt; continue
                clave = hijo_id + "|" + str(data.get("mid", ""))
                if clave in fb_ya_relayados:
                    del data, txt; continue
                fb_ya_relayados.add(clave)
                if hijo_par == NODE_ID and hijo_id not in hijos_detectados:
                    hijos_detectados.append(hijo_id)
                    _barra_status("Hijo: " + hijo_id, VERDE)
                relay_fb_hijo(en, txt)
            del data, txt
        except: pass

    gc.collect()

    if debe_responder:
        _rol = "NODO ({} hijos)".format(len(hijos_detectados)) if hijos_detectados else "HOJA"
        pl_envio = payload_completo(med_fresh)
        enviar_fb(en, pl_envio, parent_id, mid_origen=wave_mid)
        ui_nodo(t2, h2, mq2, ax2, ay2,
                status="FB ok | " + _rol, status_col=VERDE)
    else:
        target = wave_recibida_data.get("target", "ALL")
        _rol = "RELAY (target:{})".format(target[:8])
        ui_nodo(t2, h2, mq2, ax2, ay2,
                status="Solo relay (no soy target)", status_col=GRIS)
        print("[NO RESPONDE] target era:", target)
    utime.sleep_ms(800)

    cerrar_espnow(en)
    ui_sleep_screen()
    print("[Sleep] {} | {} | {}s | RAM:{}".format(
        NODE_ID, _rol, SLEEP_MS // 1000, gc.mem_free()))
    lightsleep(SLEEP_MS)
    print("[Wake]", NODE_ID)

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
gc.collect()

backlight.value(1)
detectar_sensor()
ui_modo_detectado()
gc.collect()

if MODO_SIEMPRE_DESPIERTO:
    # ─── MODO SIEMPRE-DESPIERTO ───
    # Inicializar WiFi STA + ESP-NOW UNA SOLA VEZ aquí, y nunca cerrar.
    # Después escanear in-situ usando este mismo objeto _en_global.
    # Esto evita el bug ESP_ERR_ESPNOW_NO_MEM del v15.0.
    print("[INIT] Radio permanente para modo siempre-despierto")
    _sta.active(True)
    _sta.config(channel=CANALES_SCAN[0])
    utime.sleep_ms(200)
    _en_global = espnow.ESPNow()
    _en_global.active(True)
    _en_global.add_peer(BROADCAST_MAC)
    print("[INIT] ESP-NOW activo permanente, ch:", CANALES_SCAN[0])

    # Escaneo inicial con la radio ya viva
    escanear_canal_in_situ(_en_global)
    gc.collect()

    try:
        loop_siempre_despierto()
    except Exception as e:
        print("[FATAL]", e)
        tft.fill(NEGRO)
        tft.write(font_sm, NODE_ID,        4,  4,  CYAN)
        tft.write(font_md, "ERROR",         4, 28,  ROJO)
        tft.write(font_sm, str(e)[:26],     4, 64,  BLANCO)
        tft.write(font_sm, "REINICIANDO",   4, 108, AMARILLO)
        backlight.value(1)
        utime.sleep_ms(3000)
        machine.reset()
else:
    # ─── MODO LIGHTSLEEP ───
    # En este modo el ciclo abrir/cerrar ESP-NOW es inevitable porque
    # entramos a lightsleep entre ciclos (que apaga el radio físicamente
    # y resetea el estado, así que el bug NO_MEM no se acumula).
    _sta.active(True)
    escanear_canal()
    _sta.active(False)
    gc.collect()
    while True:
        try:
            forzar = btn_der.value() == 0
            ciclo(forzar=forzar)
        except Exception as e:
            print("[ERROR]", e)
            tft.fill(NEGRO)
            tft.write(font_sm, NODE_ID,        4,  4,  CYAN)
            tft.write(font_md, "ERROR",         4, 28,  ROJO)
            tft.write(font_sm, str(e)[:26],     4, 64,  BLANCO)
            tft.write(font_sm, "REINICIANDO",   4, 108, AMARILLO)
            backlight.value(1)
            utime.sleep_ms(3000)
            machine.reset()
