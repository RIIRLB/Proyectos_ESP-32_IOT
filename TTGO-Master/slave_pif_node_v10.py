# ============================================================
#  PIF_NODE v1.0 — Plantilla Universal / LAB-ARTE
#
#  ★ ÚNICO PARÁMETRO A CAMBIAR POR DISPOSITIVO ★
#    NODE_ID = "SLAVE_XX"   (línea ~40)
#
#  Roles auto-detectados cada ciclo:
#    HOJA       → recibe WAVE, mide, envía FB al padre
#    INTERMEDIO → recibe WAVE, mide, rebroadcastea a hijos,
#                 relay de FBs, envía FB propio al padre
#
#  Modos de despertar:
#    1. Timer   — lightsleep(SLEEP_MS) caduca → ciclo normal
#    2. WAVE    — el master o un nodo padre envía WAVE
#    3. Botón   — GPIO0 (btn_izq) despierta y fuerza medición
#                 GPIO35 (btn_der) despierta y fuerza envío FB
#
#  El servidor puede pedir datos:
#    "REQ:ALL"      → WAVE a todos → este nodo responde
#    "REQ:SLAVE_01" → WAVE con target → solo SLAVE_01 responde
#
#  Display:
#    ┌─────────────────────────────┐
#    │  SLAVE_01        [hora]     │  identificación
#    │  T: 23C                     │  GRANDE amarillo
#    │  H: 65%                     │  GRANDE cyan
#    │  MQ:412  Ax:0.1 Ay:-0.1    │  otros sensores
#    │  [■ status message]         │  barra inferior
#    └─────────────────────────────┘
#
#  Memoria:
#    gc.collect() agresivo, del explícito, listas simples,
#    en.active(False)+del en+_sta.active(False) antes del sleep
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
SLEEP_MS         = 25_000   # Reposo entre ciclos (25s)
                             # El master cicla cada ~8s, con 25s el slave
                             # siempre estará despierto cuando llegue la WAVE
VENTANA_PADRE_MS = 8_000    # Espera WAVE del padre
VENTANA_HIJOS_MS = 4_000    # Espera FBs de hijos
MAX_TTL          = 6
BROADCAST_MAC    = b'\xff\xff\xff\xff\xff\xff'

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

sensor_dht = dht.DHT11(Pin(PIN_DHT11))
sensor_mq  = ADC(Pin(PIN_MQ135))
sensor_mq.atten(ADC.ATTN_11DB)
i2c        = I2C(0, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=400_000)

btn_izq = Pin(0,  Pin.IN, Pin.PULL_UP)   # GPIO0  — medir manual / wake
btn_der = Pin(35, Pin.IN, Pin.PULL_UP)   # GPIO35 — enviar FB / wake

# Despertar desde lightsleep con botón izquierdo (GPIO0)
import esp32 as _esp32
_esp32.wake_on_ext0(pin=btn_izq, level=_esp32.WAKEUP_ALL_LOW)

# ── FIX MEMORIA WIFI: Destruir instancias fantasma antes de iniciar ──
import network
try:
    _temp_sta = network.WLAN(network.STA_IF)
    _temp_sta.active(False)
    _temp_ap = network.WLAN(network.AP_IF)
    _temp_ap.active(False)
except:
    pass
# ─────────────────────────────────────────────────────────────────────

# WLAN global — reutilizado entre ciclos para no crear objeto nuevo
_sta = network.WLAN(network.STA_IF)

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
_t_cache      = "--"    # Última temp medida (persiste entre ciclos)
_h_cache      = "--"    # Última hum medida
_mq_cache     = "--"    # Último MQ
_conectado    = False   # True si recibimos WAVE en el último ciclo
_ultimo_padre = "?"     # ID del padre actual
_rol          = "?"     # "HOJA" o "NODO (N hijos)"

# ───────────────────────────────────────────────
#  DISPLAY — helpers
# ───────────────────────────────────────────────
W = tft.physical_height   # 240
H = tft.physical_width    # 135

def cx(font, txt):
    return max(0, (W - tft.write_width(font, txt)) // 2)

def _barra_status(msg, col=VERDE):
    """Barra inferior de estado — dibuja solo la última línea."""
    tft.fill_rect(0, 108, W, 27, NEGRO)
    # Cuadrito de color a la izquierda
    tft.fill_rect(4, 112, 8, 14, col)
    tft.write(font_sm, msg[:24], 16, 112, col)

def ui_nodo(t, h, mq, ax=None, ay=None, status="", status_col=VERDE):
    """
    Pantalla principal del nodo — dibuja todo de una vez.
    Layout:
      NODE_ID        hora         font_sm  CYAN
      T: XXC                      font_md  AMARILLO
      H: XX%                      font_md  CYAN
      MQ:XXX  Ax:X.X  Ay:X.X     font_sm  BLANCO
      [■ status]                  font_sm  status_col
    """
    hr, mn, seg = utime.localtime()[3:6]
    hora = "{:02d}:{:02d}:{:02d}".format(hr, mn, seg)

    tft.fill(NEGRO)
    # Fila 1: ID + hora
    tft.write(font_sm, NODE_ID, 4,   2, CYAN)
    tft.write(font_sm, hora,    160, 2, GRIS)
    # Fila 2-3: T y H grandes
    tft.write(font_md, "T: {}C".format(t), 4, 22, AMARILLO)
    tft.write(font_md, "H: {}%".format(h), 4, 52, CYAN)
    # Fila 4: otros sensores en una línea
    extras = "MQ:{}".format(mq)
    if ax is not None:
        extras += "  Ax:{:.1f}".format(ax)
    if ay is not None:
        extras += "  Ay:{:.1f}".format(ay)
    tft.write(font_sm, extras[:30], 4, 84, BLANCO)
    # Barra de estado
    tft.fill_rect(0, 108, W, 27, NEGRO)
    tft.fill_rect(4, 112, 8, 14, status_col)
    tft.write(font_sm, status[:24], 16, 112, status_col)

def ui_bienvenida():
    tft.fill(NEGRO)
    tft.write(font_md, "PIF NODE",     cx(font_md,"PIF NODE"),     4,  VERDE)
    tft.write(font_sm, "LAB-ARTE",     cx(font_sm,"LAB-ARTE"),     44, CYAN)
    tft.write(font_sm, NODE_ID,        cx(font_sm, NODE_ID),       68, AMARILLO)
    tft.write(font_sm, "v1.0",         cx(font_sm, "v1.0"),        92, GRIS)
    backlight.value(1)
    utime.sleep_ms(2000)
    backlight.value(0)

def ui_sleep_screen():
    """Pantalla mínima antes de apagar el backlight."""
    tft.fill(NEGRO)
    tft.write(font_sm, NODE_ID,      cx(font_sm, NODE_ID),      40, CYAN)
    con_txt = "conectado" if _conectado else "sin señal"
    con_col = VERDE if _conectado else GRIS
    tft.write(font_sm, con_txt,      cx(font_sm, con_txt),      66, con_col)
    tft.write(font_sm, "sleeping...", cx(font_sm, "sleeping..."), 92, GRIS)
    backlight.value(0)

# ───────────────────────────────────────────────
#  MPU6050
# ───────────────────────────────────────────────
def _s16(v):
    return v if v < 32768 else v - 65536

def mpu_init():
    try:
        i2c.writeto_mem(MPU_ADDR, 0x6B, b'\x00')
        utime.sleep_ms(80)
    except:
        pass

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
    """
    Lee todos los sensores. Actualiza cache global.
    Retorna (mediciones_lista, (t, h, mq, ax, ay))
    """
    global _t_cache, _h_cache, _mq_cache
    med = []

    try:
        sensor_dht.measure()
        t = sensor_dht.temperature()
        h = sensor_dht.humidity()
    except:
        t, h = "Err", "Err"

    _t_cache = t
    _h_cache = h
    med.append({"t": "Temp", "v": t})
    med.append({"t": "Hum",  "v": h})

    try:
        mq = sensor_mq.read()
    except:
        mq = "Err"
    _mq_cache = mq
    med.append({"t": "MQ135", "v": mq})

    ax, ay, az = mpu_leer()
    if ax is not None:
        med.append({"t": "AccX", "v": ax})
        med.append({"t": "AccY", "v": ay})
        med.append({"t": "AccZ", "v": az})

    return med, (t, h, mq, ax, ay)

# ───────────────────────────────────────────────
#  ESP-NOW
# ───────────────────────────────────────────────
def init_espnow():
    gc.collect()
    _sta.active(True)
    _sta.config(channel=6)
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)
    return en

def cerrar_espnow(en):
    """Secuencia completa de limpieza para evitar OOM en el siguiente ciclo."""
    try:    en.active(False)
    except: pass
    try:    del en
    except: pass
    try:    _sta.active(False)
    except: pass
    gc.collect()

def enviar_fb(en, mediciones, parent_id):
    pkt = json.dumps({
        "type": "FB",
        "id"  : NODE_ID,
        "par" : parent_id,
        "pl"  : mediciones
    })
    # Si el paquete excede 248 bytes, enviar solo los 3 principales
    if len(pkt) > 248:
        pkt = json.dumps({
            "type": "FB",
            "id"  : NODE_ID,
            "par" : parent_id,
            "pl"  : mediciones[:3]
        })
    try:
        en.send(BROADCAST_MAC, pkt)
        print("[FB] → padre:{} bytes:{}".format(parent_id, len(pkt)))
    except Exception as e:
        print("[FB ERR]", e)

def relay_fb_hijo(en, raw_str):
    """Reenvía FB de un hijo con campo 'via' para evitar bucles."""
    try:
        data = json.loads(raw_str)
        via  = data.get("via", [])
        if NODE_ID in via:
            del data
            return
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
def ciclo(forzar_medicion=False):
    """
    forzar_medicion=True → mide y envía FB sin esperar WAVE
                           (usado cuando se presiona un botón)
    """
    global _conectado, _ultimo_padre, _rol

    gc.collect()
    backlight.value(1)
    mpu_init()

    # ── Caso especial: botón presionado ────────
    if forzar_medicion:
        ui_nodo(_t_cache, _h_cache, _mq_cache,
                status="Midiendo...", status_col=AMARILLO)

        med, (t, h, mq, ax, ay) = leer_sensores()
        ui_nodo(t, h, mq, ax, ay,
                status="Enviando FB...", status_col=AZUL)
        utime.sleep_ms(300)

        en = init_espnow()
        # Enviar FB sin padre conocido → broadcast general
        padre = _ultimo_padre if _ultimo_padre != "?" else "MASTER_TTGO_GATEWAY"
        enviar_fb(en, med, padre)
        ui_nodo(t, h, mq, ax, ay,
                status="FB enviado por boton", status_col=VERDE)
        utime.sleep_ms(1500)
        cerrar_espnow(en)
        ui_sleep_screen()
        lightsleep(SLEEP_MS)
        return

    # ── Paso 1: Escuchar WAVE del padre ────────
    ui_nodo(_t_cache, _h_cache, _mq_cache,
            status="Esperando WAVE...", status_col=GRIS)
    en = init_espnow()

    wave_recibida = False
    parent_id     = None
    wave_cmd      = "REQ:ALL"
    wave_ttl      = MAX_TTL

    fin = utime.ticks_add(utime.ticks_ms(), VENTANA_PADRE_MS)
    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:

        # Botón izquierdo durante espera → medición manual
        if btn_izq.value() == 0:
            utime.sleep_ms(50)
            if btn_izq.value() == 0:
                med, (t, h, mq, ax, ay) = leer_sensores()
                ui_nodo(t, h, mq, ax, ay,
                        status="Medicion manual", status_col=AMARILLO)
                utime.sleep_ms(2000)
                gc.collect()

        host, msg = en.recv(50)
        if not msg:
            continue

        try:
            txt  = msg.decode()
            data = json.loads(txt)

            if data.get("type") == "WAVE":
                ttl    = data.get("ttl", MAX_TTL)
                target = data.get("target", "ALL")

                if ttl <= 0:
                    del data; continue
                if target != "ALL" and target != NODE_ID:
                    del data; continue

                parent_id     = data.get("from", "MASTER_TTGO_GATEWAY")
                wave_cmd      = data.get("cmd",  "REQ:ALL")
                wave_ttl      = ttl
                wave_recibida = True
                _conectado    = True
                _ultimo_padre = parent_id
                print("[WAVE] De:{} cmd:{} ttl:{}".format(parent_id, wave_cmd, ttl))
                del data
                break

            del data
        except:
            pass

    # ── Sin WAVE → dormir ──────────────────────
    if not wave_recibida:
        _conectado = False
        ui_nodo(_t_cache, _h_cache, _mq_cache,
                status="Sin WAVE - durmiendo", status_col=ROJO)
        utime.sleep_ms(600)
        cerrar_espnow(en)
        ui_sleep_screen()
        lightsleep(SLEEP_MS)
        return

    # ── Paso 2: Medir ──────────────────────────
    ui_nodo(_t_cache, _h_cache, _mq_cache,
            status="Midiendo sensores...", status_col=AMARILLO)
    med, (t, h, mq, ax, ay) = leer_sensores()
    ui_nodo(t, h, mq, ax, ay,
            status="Conectado a " + parent_id[:16], status_col=VERDE)
    utime.sleep_ms(400)

    # ── Paso 3: Propagar WAVE a hijos ──────────
    if wave_ttl > 1:
        wave_hijos = json.dumps({
            "type"  : "WAVE",
            "cmd"   : wave_cmd,
            "from"  : NODE_ID,
            "target": "ALL",
            "ttl"   : wave_ttl - 1
        })
        _barra_status("Propagando WAVE ttl:{}".format(wave_ttl - 1), AMARILLO)
        en.send(BROADCAST_MAC, wave_hijos)
        utime.sleep_ms(120)
        en.send(BROADCAST_MAC, wave_hijos)
        del wave_hijos
        print("[WAVE] Propagada ttl:", wave_ttl - 1)
    else:
        print("[WAVE] TTL agotado, soy hoja")

    gc.collect()

    # ── Paso 4: Escuchar FBs de hijos ──────────
    _barra_status("Esperando hijos...", CYAN)

    hijos_detectados = []
    fb_ya_relayados  = []

    fin_hijos = utime.ticks_add(utime.ticks_ms(), VENTANA_HIJOS_MS)
    while utime.ticks_diff(fin_hijos, utime.ticks_ms()) > 0:
        host, msg = en.recv(20)
        if not msg:
            continue
        try:
            txt  = msg.decode()
            data = json.loads(txt)

            if data.get("type") == "FB":
                hijo_id  = data.get("id",  "?")
                hijo_par = data.get("par", "")

                if hijo_par == NODE_ID:
                    if hijo_id not in hijos_detectados:
                        hijos_detectados.append(hijo_id)
                        _barra_status("Hijo: " + hijo_id, VERDE)

                    if hijo_id not in fb_ya_relayados:
                        fb_ya_relayados.append(hijo_id)
                        relay_fb_hijo(en, txt)

            del data, txt
        except:
            pass

    gc.collect()

    # ── Paso 5: Enviar FB propio ───────────────
    _rol = "NODO ({} hijos)".format(len(hijos_detectados)) if hijos_detectados else "HOJA"

    enviar_fb(en, med, parent_id)
    ui_nodo(t, h, mq, ax, ay,
            status="FB enviado | " + _rol, status_col=VERDE)
    utime.sleep_ms(800)

    # ── Paso 6: Cerrar radio y dormir ──────────
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

# Detectar causa de wake:
#   machine.PWRON_RESET = encendido normal
#   machine.SOFT_RESET  = soft reset
#   ESP32 wake_reason() = 4 si fue ext0 (botón)
_wake = machine.reset_cause()
_boton_wake = (_wake == machine.SOFT_RESET and btn_izq.value() == 0) or \
              btn_der.value() == 0

while True:
    try:
        # Si se despertó con el botón derecho → forzar envío
        forzar = btn_der.value() == 0
        ciclo(forzar_medicion=forzar)
    except Exception as e:
        print("[ERROR]", e)
        # Mostrar error con datos actuales en pantalla
        tft.fill(NEGRO)
        tft.write(font_sm, NODE_ID,       4,  4,  CYAN)
        tft.write(font_md, "ERROR",        4,  28, ROJO)
        tft.write(font_sm, str(e)[:26],    4,  64, BLANCO)
        tft.write(font_sm, "durmiendo...", 4, 108, GRIS)
        backlight.value(1)
        try:
            _sta.active(False)
        except:
            pass
        gc.collect()
        utime.sleep_ms(3000)
        backlight.value(0)
        lightsleep(SLEEP_MS)
