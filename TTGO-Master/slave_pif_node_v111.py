# ============================================================
#  PIF_NODE v11.0 — Plantilla Universal / LAB-ARTE
#
#  ★ ÚNICO PARÁMETRO A CAMBIAR POR DISPOSITIVO ★
#    NODE_ID = "SLAVE_XX"   (línea ~45)
#
#  Modos de operación (coexisten):
#
#  A) AUTÓNOMO cada SLEEP_MS (10s)
#     Mide todos los sensores → guarda en buffer local (máx 5)
#     → envía FB unsolicited → duerme
#
#  B) BAJO DEMANDA (WAVE del Master)
#     Recibe WAVE → mide en tiempo real → envía FB con buffer
#     → propaga WAVE a hijos (SIEMPRE si ttl>1)
#     → espera FBs de hijos y los hace relay → duerme
#
#  C) POR BOTÓN (GPIO35 o GPIO0)
#     Mide en tiempo real → envía FB inmediato → limpia buffer → duerme
#
#  Errores de sensor:
#     Si un sensor falla, el FB incluye {"t":"Tipo","v":"ERR"}
#     No se descartan silenciosamente.
#
#  Canal automático:
#     Al arrancar escanea ch 1, 6 y 11 buscando una WAVE del Master.
#     Se sincroniza al canal donde la recibe.
#     Si pasan CANAL_MISS_MAX ciclos sin WAVE, re-escanea.
#
#  Propagación de WAVE:
#     Un nodo propaga SIEMPRE si ttl>1, sin importar si es el target.
#     Así un REQ:SLAVE_03 llega aunque esté detrás de SLAVE_01.
# ============================================================

import gc, network, espnow, machine, utime, json
from machine import Pin, lightsleep, I2C, ADC
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
#  TIEMPOS
# ───────────────────────────────────────────────
SLEEP_MS         = 10_000   # Reposo entre ciclos autónomos (10s)
VENTANA_WAVE_MS  = 2_000    # Espera WAVE después de enviar FB autónomo
VENTANA_HIJOS_MS = 3_000    # Espera FBs de hijos tras propagar WAVE
CANAL_SCAN_MS    = 2_000    # Tiempo por canal durante el escaneo
CANAL_MISS_MAX   = 3        # Ciclos sin WAVE antes de re-escanear canal
BUFFER_MAX       = 5        # Máximo de lecturas acumuladas en buffer
MAX_TTL          = 6
BROADCAST_MAC    = b'\xff\xff\xff\xff\xff\xff'
CANALES_SCAN     = [1, 6, 11]   # Canales WiFi más comunes en México

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

import esp32 as _esp32
_esp32.wake_on_ext0(pin=btn_izq, level=_esp32.WAKEUP_ALL_LOW)

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
_canal_actual = 6        # canal detectado por escaneo
_ciclos_sin_wave = 0     # contador para re-escaneo de canal
_buffer       = []       # lecturas autónomas acumuladas: [{"ts":..., "pl":[...]}]

# ───────────────────────────────────────────────
#  RADIO
# ───────────────────────────────────────────────
_sta = network.WLAN(network.STA_IF)

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
    try:    _sta.active(False)
    except: pass
    gc.collect()

# ───────────────────────────────────────────────
#  ESCANEO DE CANAL
#  Prueba cada canal de CANALES_SCAN durante CANAL_SCAN_MS.
#  Si recibe una WAVE, lee el campo "ch" y lo adopta.
#  Si no recibe nada en ningún canal, mantiene el canal actual.
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
                    # Adoptar el canal que el Master indica en su WAVE
                    ch_master = data.get("ch", ch)
                    _canal_actual = ch_master
                    encontrado = True
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
#  Siempre devuelve mediciones — ERR si el sensor falla.
#  Los valores ERR se reportan al servidor (no se descartan).
# ───────────────────────────────────────────────
def leer_sensores():
    global _t_cache, _h_cache, _mq_cache
    med = []

    # DHT11 — hasta 3 intentos con pausa de estabilización
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

    # MQ135
    try:
        mq = sensor_mq.read()
    except:
        mq = "ERR"
    _mq_cache = mq
    med.append({"t": "MQ135", "v": mq})

    # MPU6050
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
    t_col = ROJO if t == "ERR" else AMARILLO
    h_col = ROJO if h == "ERR" else CYAN
    tft.fill(NEGRO)
    tft.write(font_sm, NODE_ID, 4,   2, CYAN)
    tft.write(font_sm, hora,    160, 2, GRIS)
    tft.write(font_md, "T: {}C".format(t), 4, 22, t_col)
    tft.write(font_md, "H: {}%".format(h), 4, 52, h_col)
    extras = "MQ:{}".format(mq)
    if ax is not None:
        extras += "  Ax:{:.1f}".format(ax)
    if ay is not None:
        extras += "  Ay:{:.1f}".format(ay)
    tft.write(font_sm, extras[:30], 4, 84, BLANCO)
    tft.fill_rect(0, 108, W, 27, NEGRO)
    tft.fill_rect(4, 112, 8, 14, status_col)
    tft.write(font_sm, status[:24], 16, 112, status_col)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF NODE",  cx(font_md, "PIF NODE"),  4,  VERDE)
    tft.write(font_sm, "LAB-ARTE",  cx(font_sm, "LAB-ARTE"),  44, CYAN)
    tft.write(font_sm, NODE_ID,     cx(font_sm, NODE_ID),     68, AMARILLO)
    tft.write(font_sm, "v11.0",     cx(font_sm, "v11.0"),     92, GRIS)
    backlight.value(1)
    utime.sleep_ms(2000)
    backlight.value(0)

def ui_sleep_screen():
    tft.fill(NEGRO)
    tft.write(font_sm, NODE_ID,      cx(font_sm, NODE_ID),      40, CYAN)
    con_txt = "conectado" if _conectado else "sin señal"
    con_col = VERDE if _conectado else GRIS
    tft.write(font_sm, con_txt,      cx(font_sm, con_txt),      66, con_col)
    tft.write(font_sm, "sleeping...", cx(font_sm, "sleeping..."), 92, GRIS)
    backlight.value(0)

# ───────────────────────────────────────────────
#  BUFFER LOCAL
# ───────────────────────────────────────────────
def agregar_al_buffer(mediciones):
    """Agrega una lectura al buffer. Descarta la más antigua si supera BUFFER_MAX."""
    hr, mn, seg = utime.localtime()[3:6]
    ts = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)
    _buffer.append({"ts": ts, "pl": mediciones})
    while len(_buffer) > BUFFER_MAX:
        _buffer.pop(0)

def payload_completo(mediciones_fresh):
    """Devuelve un payload combinando buffer acumulado + lectura fresca.
    El buffer se limpia tras enviarlo."""
    combined = []
    # Incluir las lecturas del buffer con timestamp
    for entry in _buffer:
        for m in entry["pl"]:
            combined.append({
                "t"  : m["t"],
                "v"  : m["v"],
                "ts" : entry["ts"]
            })
    # Agregar lectura fresca (sin ts para que el Master use su hora)
    for m in mediciones_fresh:
        combined.append({"t": m["t"], "v": m["v"]})
    _buffer.clear()
    return combined

# ───────────────────────────────────────────────
#  ESP-NOW — envío y relay
# ───────────────────────────────────────────────
def enviar_fb(en, payload, parent_id):
    pkt = json.dumps({
        "type": "FB",
        "id"  : NODE_ID,
        "par" : parent_id,
        "pl"  : payload
    })
    if len(pkt) > 248:
        # Truncar payload si excede el límite ESP-NOW
        pkt = json.dumps({
            "type": "FB",
            "id"  : NODE_ID,
            "par" : parent_id,
            "pl"  : payload[:3]
        })
    try:
        en.send(BROADCAST_MAC, pkt)
        print("[FB] → {}  bytes:{}".format(parent_id, len(pkt)))
    except Exception as e:
        print("[FB ERR]", e)

def relay_fb_hijo(en, raw_str):
    try:
        data = json.loads(raw_str)
        via  = data.get("via", [])
        if NODE_ID in via:
            del data; return
        via.append(NODE_ID)
        data["via"] = via
        relay = json.dumps(data)
        if len(relay) < 248:
            en.send(BROADCAST_MAC, relay)
            print("[RELAY] ← hijo:", data.get("id", "?"))
        del data, relay
    except Exception as e:
        print("[RELAY ERR]", e)

# ───────────────────────────────────────────────
#  CICLO PRINCIPAL
# ───────────────────────────────────────────────
def ciclo(forzar=False):
    """
    forzar=True → medición + envío inmediato (botón).
    forzar=False → ciclo normal: mide, envía autónomo, escucha WAVE.
    """
    global _conectado, _ultimo_padre, _rol, _ciclos_sin_wave, _canal_actual

    gc.collect()
    backlight.value(1)
    mpu_init()

    # ── Caso botón ─────────────────────────────
    if forzar:
        ui_nodo(_t_cache, _h_cache, _mq_cache,
                status="Midiendo...", status_col=AMARILLO)
        med, (t, h, mq, ax, ay) = leer_sensores()
        ui_nodo(t, h, mq, ax, ay, status="Enviando FB...", status_col=AZUL)
        en = init_espnow(_canal_actual)
        enviar_fb(en, med, _ultimo_padre)
        _buffer.clear()
        ui_nodo(t, h, mq, ax, ay, status="Enviado (boton)", status_col=VERDE)
        utime.sleep_ms(1500)
        cerrar_espnow(en)
        ui_sleep_screen()
        lightsleep(SLEEP_MS)
        return

    # ── Paso 1: Medir autónomo ──────────────────
    ui_nodo(_t_cache, _h_cache, _mq_cache,
            status="Midiendo (auto)...", status_col=GRIS)
    med, (t, h, mq, ax, ay) = leer_sensores()
    agregar_al_buffer(med)
    ui_nodo(t, h, mq, ax, ay,
            status="Buffer: {}".format(len(_buffer)), status_col=GRIS)

    # ── Paso 2: Enviar FB autónomo ──────────────
    en = init_espnow(_canal_actual)
    enviar_fb(en, med, _ultimo_padre)
    print("[AUTO] FB enviado | buffer:", len(_buffer), "| canal:", _canal_actual)

    # ── Paso 3: Escuchar WAVE brevemente ────────
    # Ventana corta — si hay una WAVE pendiente del Master, la atendemos.
    _barra_status("Escuchando WAVE...", GRIS)
    wave_recibida  = False
    parent_id      = None
    wave_cmd       = "REQ:ALL"
    wave_ttl       = MAX_TTL

    fin = utime.ticks_add(utime.ticks_ms(), VENTANA_WAVE_MS)
    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        try: host, msg = en.recv(30)
        except: continue
        if not msg: continue
        try:
            txt  = msg.decode()
            data = json.loads(txt)
            if data.get("type") == "WAVE":
                ttl    = data.get("ttl", MAX_TTL)
                target = data.get("target", "ALL")
                if ttl <= 0:
                    del data; continue
                # Actualizar canal si el Master indica uno diferente
                ch_master = data.get("ch", _canal_actual)
                if ch_master != _canal_actual:
                    _canal_actual = ch_master
                    print("[CH] Actualizado a", _canal_actual)
                parent_id     = data.get("from", "MASTER_TTGO_GATEWAY")
                wave_cmd      = data.get("cmd", "REQ:ALL")
                wave_ttl      = ttl
                wave_recibida = True
                _conectado    = True
                _ultimo_padre = parent_id
                _ciclos_sin_wave = 0
                print("[WAVE] De:{} cmd:{} ttl:{}".format(parent_id, wave_cmd, ttl))
                del data; break
            del data
        except: pass

    # ── Sin WAVE — solo dormimos ────────────────
    if not wave_recibida:
        _ciclos_sin_wave += 1
        _conectado = False
        print("[NO WAVE] ciclos sin wave:", _ciclos_sin_wave)
        cerrar_espnow(en)
        # Re-escanear canal si llevamos demasiados ciclos sin contacto
        if _ciclos_sin_wave >= CANAL_MISS_MAX:
            backlight.value(1)
            escanear_canal()
            _ciclos_sin_wave = 0
        ui_sleep_screen()
        lightsleep(SLEEP_MS)
        return

    # ── Paso 4: Medir fresco para la WAVE ──────
    ui_nodo(t, h, mq, ax, ay,
            status="WAVE recibida", status_col=VERDE)
    med_fresh, (t2, h2, mq2, ax2, ay2) = leer_sensores()
    ui_nodo(t2, h2, mq2, ax2, ay2,
            status="Conectado: " + parent_id[:14], status_col=VERDE)
    utime.sleep_ms(300)

    # ── Paso 5: Propagar WAVE a hijos ──────────
    # SIEMPRE propagamos si ttl>1, independientemente del target.
    # Esto permite que REQ:SLAVE_N llegue a través de nodos intermedios.
    if wave_ttl > 1:
        wave_hijos = json.dumps({
            "type"  : "WAVE",
            "cmd"   : wave_cmd,
            "from"  : NODE_ID,
            "target": target,
            "ttl"   : wave_ttl - 1,
            "ch"    : _canal_actual
        })
        _barra_status("Propagando ttl:{}".format(wave_ttl - 1), AMARILLO)
        en.send(BROADCAST_MAC, wave_hijos)
        utime.sleep_ms(120)
        en.send(BROADCAST_MAC, wave_hijos)
        del wave_hijos
        print("[WAVE] Propagada ttl:", wave_ttl - 1)

    gc.collect()

    # ── Paso 6: Escuchar FBs de hijos ──────────
    _barra_status("Esperando hijos...", CYAN)
    hijos_detectados = []
    fb_ya_relayados  = []

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
                if hijo_par == NODE_ID:
                    if hijo_id not in hijos_detectados:
                        hijos_detectados.append(hijo_id)
                        _barra_status("Hijo: " + hijo_id, VERDE)
                    if hijo_id not in fb_ya_relayados:
                        fb_ya_relayados.append(hijo_id)
                        relay_fb_hijo(en, txt)
            del data, txt
        except: pass

    gc.collect()

    # ── Paso 7: Enviar FB propio con buffer ─────
    _rol = "NODO ({} hijos)".format(len(hijos_detectados)) if hijos_detectados else "HOJA"
    pl_envio = payload_completo(med_fresh)
    enviar_fb(en, pl_envio, parent_id)
    ui_nodo(t2, h2, mq2, ax2, ay2,
            status="FB ok | " + _rol, status_col=VERDE)
    utime.sleep_ms(800)

    # ── Paso 8: Cerrar radio y dormir ──────────
    cerrar_espnow(en)
    ui_sleep_screen()
    print("[Sleep] {} | {} | {}s | RAM:{}".format(
        NODE_ID, _rol, SLEEP_MS // 1000, gc.mem_free()))
    lightsleep(SLEEP_MS)
    print("[Wake]", NODE_ID)

# ───────────────────────────────────────────────
#  ARRANQUE Y BUCLE PRINCIPAL
# ───────────────────────────────────────────────
ui_bienvenida()
gc.collect()

# Escaneo inicial de canal
backlight.value(1)
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
