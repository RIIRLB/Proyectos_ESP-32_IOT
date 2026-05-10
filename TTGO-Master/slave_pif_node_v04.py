# ============================================================
#  PIF_NODE v0.4 — Nodo Genérico de la Malla PIF / LAB-ARTE
#
#  Correcciones v0.4 — "WiFi Out of Memory":
#   [FIX-MEM-1] cerrar_espnow() ahora hace:
#               en.active(False) → del en → sta.active(False) → gc.collect()
#               Sin esto el objeto ESPNow quedaba vivo en RAM cada ciclo.
#   [FIX-MEM-2] init_espnow() hace gc.collect() ANTES de crear objetos nuevos.
#   [FIX-MEM-3] msg.decode() se llama una sola vez por mensaje (antes 2×).
#   [FIX-MEM-4] _sta global: un solo objeto WLAN reutilizado, no uno nuevo
#               por ciclo.
#   [FIX-MEM-5] s() movida fuera de mpu_leer() — evita crear una función
#               nueva en cada llamada.
#   [FIX-MEM-6] gc.collect() al inicio de cada ciclo y tras bucles pesados.
#   [FIX-MEM-7] fb_ya_relayados como lista simple (set() es caro en uPy).
#   [FIX-MEM-8] json.dumps de WAVE calculado una sola vez, no dentro del loop.
#
# ============================================================
#  ÚNICO PARÁMETRO A CAMBIAR POR DISPOSITIVO:
#    NODE_ID = "SLAVE_XX"
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
#  ★ ÚNICO CAMBIO POR DISPOSITIVO ★
# ───────────────────────────────────────────────
NODE_ID = "SLAVE_01"   # ← cambiar en cada TTGO

# ───────────────────────────────────────────────
#  CONFIGURACIÓN DE TIEMPOS
# ───────────────────────────────────────────────
SLEEP_MS         = 30_000
VENTANA_PADRE_MS = 6_000
VENTANA_HIJOS_MS = 4_000
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
#  HARDWARE — inicializado una sola vez al arranque
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
backlight = Pin(4, Pin.OUT)
backlight.value(1)

sensor_dht = dht.DHT11(Pin(PIN_DHT11))
sensor_mq  = ADC(Pin(PIN_MQ135))
sensor_mq.atten(ADC.ATTN_11DB)
i2c        = I2C(0, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=400_000)

btn_izq = Pin(0,  Pin.IN, Pin.PULL_UP)
btn_der = Pin(35, Pin.IN, Pin.PULL_UP)

# [FIX-MEM-4] WLAN global — se reutiliza entre ciclos, no se recrea
_sta = network.WLAN(network.STA_IF)

# ───────────────────────────────────────────────
#  DISPLAY
# ───────────────────────────────────────────────
W = tft.physical_height   # 240
H = tft.physical_width    # 135

def cx(font, txt):
    return max(0, (W - tft.write_width(font, txt)) // 2)

def ui(titulo, l1="", l2="", l3="", ct=st7789.CYAN):
    tft.fill(st7789.BLACK)
    tft.write(font_md, titulo, cx(font_md, titulo), 4, ct)
    if l1: tft.write(font_sm, l1, cx(font_sm, l1), 42, st7789.WHITE)
    if l2: tft.write(font_sm, l2, cx(font_sm, l2), 68, st7789.WHITE)
    if l3: tft.write(font_sm, l3, cx(font_sm, l3), 94, st7789.YELLOW)

def ui_bienvenida():
    tft.fill(st7789.BLACK)
    tft.write(font_md, "PIF NODE",     cx(font_md, "PIF NODE"),     4,  st7789.GREEN)
    tft.write(font_sm, "LAB-ARTE",     cx(font_sm, "LAB-ARTE"),     44, st7789.CYAN)
    tft.write(font_sm, NODE_ID,        cx(font_sm, NODE_ID),        70, st7789.YELLOW)
    tft.write(font_sm, "v0.4",         cx(font_sm, "v0.4"),         96, st7789.WHITE)

def ui_sensor(t, h, mq, rol=""):
    tft.fill(st7789.BLACK)
    tft.write(font_sm, NODE_ID,                  cx(font_sm, NODE_ID),                  4,  st7789.CYAN)
    l1 = "T:{}C  H:{}%".format(t, h)
    tft.write(font_sm, l1,                       cx(font_sm, l1),                       30, st7789.WHITE)
    l2 = "MQ:{}".format(mq)
    tft.write(font_sm, l2,                       cx(font_sm, l2),                       56, st7789.WHITE)
    if rol:
        tft.write(font_sm, rol,                  cx(font_sm, rol),                      82, st7789.YELLOW)

def ui_sleep():
    tft.fill(st7789.BLACK)
    tft.write(font_sm, NODE_ID,        cx(font_sm, NODE_ID),        44, st7789.CYAN)
    tft.write(font_sm, "sleeping...", cx(font_sm, "sleeping..."), 70, st7789.WHITE)
    backlight.value(0)

# ───────────────────────────────────────────────
#  MPU6050
#  [FIX-MEM-5] s() definida aquí arriba, no dentro de mpu_leer()
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
        ax = round(_s16(raw[0] << 8 | raw[1]) / 16384.0, 2)
        ay = round(_s16(raw[2] << 8 | raw[3]) / 16384.0, 2)
        az = round(_s16(raw[4] << 8 | raw[5]) / 16384.0, 2)
        return ax, ay, az
    except:
        return None, None, None

# ───────────────────────────────────────────────
#  SENSORES
# ───────────────────────────────────────────────
def leer_sensores():
    med = []
    try:
        sensor_dht.measure()
        t = sensor_dht.temperature()
        h = sensor_dht.humidity()
    except:
        t, h = "Err", "Err"
    med.append({"t": "Temp",  "v": t})
    med.append({"t": "Hum",   "v": h})

    try:
        mq = sensor_mq.read()
    except:
        mq = "Err"
    med.append({"t": "MQ135", "v": mq})

    ax, ay, az = mpu_leer()
    if ax is not None:
        med.append({"t": "AccX", "v": ax})
        med.append({"t": "AccY", "v": ay})
        med.append({"t": "AccZ", "v": az})

    return med, (t, h, mq)

# ───────────────────────────────────────────────
#  ESP-NOW — init y cierre seguros
# ───────────────────────────────────────────────
def init_espnow():
    """
    [FIX-MEM-2] gc.collect() ANTES de crear objetos nuevos.
    Reutiliza _sta global en lugar de crear un WLAN nuevo.
    """
    gc.collect()
    _sta.active(True)
    _sta.config(channel=6)
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)
    return en

def cerrar_espnow(en):
    """
    [FIX-MEM-1] Secuencia completa de liberación de memoria:
      1. Desactivar ESPNow
      2. Eliminar la referencia (permite al GC recuperar la RAM)
      3. Apagar el radio STA (libera buffer del driver WiFi)
      4. Forzar recolección de basura
    Sin estos pasos, cada ciclo consumía ~20-40 KB que nunca se devolvían.
    """
    try:
        en.active(False)
    except:
        pass
    try:
        del en
    except:
        pass
    try:
        _sta.active(False)
    except:
        pass
    gc.collect()

def enviar_feedback(en, mediciones, parent_id):
    pkt = json.dumps({
        "type": "FB",
        "id"  : NODE_ID,
        "par" : parent_id,
        "pl"  : mediciones
    })
    if len(pkt) > 248:
        pkt = json.dumps({
            "type": "FB",
            "id"  : NODE_ID,
            "par" : parent_id,
            "pl"  : mediciones[:3]
        })
    en.send(BROADCAST_MAC, pkt)
    print("[FB] Enviado | padre:", parent_id, "| bytes:", len(pkt))

def reenviar_fb_hijo(en, raw_str):
    """
    Relay de FBs de hijos hacia el padre.
    Usa campo 'via' para evitar bucles sin bloquear multi-salto.
    """
    try:
        data = json.loads(raw_str)
        via  = data.get("via", [])
        if NODE_ID in via:
            return   # Ya pasó por este nodo, ignorar
        via.append(NODE_ID)
        data["via"] = via
        relay = json.dumps(data)
        if len(relay) < 248:
            en.send(BROADCAST_MAC, relay)
            print("[RELAY] FB de:", data.get("id", "?"))
        del data, relay
    except Exception as e:
        print("[RELAY ERROR]", e)

# ───────────────────────────────────────────────
#  CICLO PRINCIPAL
# ───────────────────────────────────────────────
def ciclo():
    # [FIX-MEM-6] GC al inicio de cada ciclo, antes de cualquier alloc
    gc.collect()
    backlight.value(1)
    mpu_init()

    # ── Paso 1: Esperar WAVE del padre ─────────
    ui("ESCUCHA", "Esperando WAVE...", NODE_ID)
    en = init_espnow()

    wave_recibida = False
    parent_id     = None
    wave_cmd      = None
    wave_ttl      = 0

    fin = utime.ticks_add(utime.ticks_ms(), VENTANA_PADRE_MS)
    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:

        if btn_izq.value() == 0:
            utime.sleep_ms(50)
            if btn_izq.value() == 0:
                ui("MANUAL", "Midiendo...", NODE_ID, ct=st7789.YELLOW)
                utime.sleep_ms(300)
                med, (t, h, mq) = leer_sensores()
                ui_sensor(t, h, mq, rol="SIN CONEXION")
                utime.sleep_ms(2000)
                gc.collect()

        host, msg = en.recv(50)
        if not msg:
            continue

        # [FIX-MEM-3] decode() una sola vez
        try:
            txt  = msg.decode()
            data = json.loads(txt)

            if data.get("type") == "WAVE":
                ttl = data.get("ttl", MAX_TTL)
                if ttl <= 0:
                    del data
                    continue

                target = data.get("target", "ALL")
                if target != "ALL" and target != NODE_ID:
                    del data
                    continue

                parent_id     = data.get("from", "MASTER")
                wave_cmd      = data.get("cmd",  "REQ:ALL")
                wave_ttl      = ttl
                wave_recibida = True
                print("[WAVE] De:", parent_id, "| cmd:", wave_cmd, "| ttl:", ttl)
                del data
                break

            del data
        except:
            pass

    if not wave_recibida:
        ui("SIN SEÑAL", "No llego WAVE", "Durmiendo...", ct=st7789.RED)
        cerrar_espnow(en)
        utime.sleep_ms(800)
        ui_sleep()
        lightsleep(SLEEP_MS)
        return

    # ── Paso 2: Medir sensores propios ─────────
    ui("MIDIENDO", NODE_ID, "padre:" + parent_id[:14], ct=st7789.YELLOW)
    mediciones, (t, h, mq) = leer_sensores()
    ui_sensor(t, h, mq)
    utime.sleep_ms(400)

    # ── Paso 3: Propagar WAVE a hijos ──────────
    if wave_ttl > 1:
        # [FIX-MEM-8] JSON calculado una sola vez antes del bucle
        wave_hijos = json.dumps({
            "type"  : "WAVE",
            "cmd"   : wave_cmd,
            "from"  : NODE_ID,
            "target": "ALL",
            "ttl"   : wave_ttl - 1
        })
        ui("PROPAGANDO", "Onda a hijos...", "ttl:{}".format(wave_ttl - 1), ct=st7789.YELLOW)
        en.send(BROADCAST_MAC, wave_hijos)
        utime.sleep_ms(120)
        en.send(BROADCAST_MAC, wave_hijos)
        del wave_hijos
        print("[WAVE] Propagada | ttl:", wave_ttl - 1)
    else:
        print("[WAVE] TTL agotado, no propagar")

    gc.collect()   # [FIX-MEM-6] Limpiar tras operaciones JSON

    # ── Paso 4: Escuchar FBs de hijos ──────────
    ui("ESCUCHA", "Esperando hijos...", NODE_ID, ct=st7789.CYAN)

    hijos_detectados = []   # IDs únicos de hijos
    # [FIX-MEM-7] Lista simple en vez de set() (set es caro en MicroPython)
    fb_ya_relayados  = []

    fin_hijos = utime.ticks_add(utime.ticks_ms(), VENTANA_HIJOS_MS)

    while utime.ticks_diff(fin_hijos, utime.ticks_ms()) > 0:
        host, msg = en.recv(20)
        if not msg:
            continue
        try:
            # [FIX-MEM-3] decode() una sola vez
            txt  = msg.decode()
            data = json.loads(txt)

            if data.get("type") == "FB":
                hijo_id  = data.get("id",  "?")
                hijo_par = data.get("par", "")

                if hijo_par == NODE_ID:
                    if hijo_id not in hijos_detectados:
                        hijos_detectados.append(hijo_id)
                        ui("HIJO OK", hijo_id,
                           "{} hijos".format(len(hijos_detectados)),
                           ct=st7789.GREEN)
                    # Relay deduplicado — sin set(), solo lista
                    if hijo_id not in fb_ya_relayados:
                        fb_ya_relayados.append(hijo_id)
                        reenviar_fb_hijo(en, txt)

            del data, txt
        except:
            pass

    gc.collect()   # [FIX-MEM-6] Tras el bucle de escucha

    # ── Paso 5: Reportar rol y enviar FB propio ─
    rol = "NODO ({} hijos)".format(len(hijos_detectados)) if hijos_detectados else "HOJA"

    ui_sensor(t, h, mq, rol=rol)
    utime.sleep_ms(500)

    enviar_feedback(en, mediciones, parent_id)
    ui("FB ENVIADO", rol, "→ " + parent_id[:14], ct=st7789.GREEN)
    utime.sleep_ms(800)

    # ── Paso 6: Cerrar radio y dormir ──────────
    # [FIX-MEM-1] cerrar_espnow libera todo antes del sleep
    cerrar_espnow(en)
    ui_sleep()
    print("[Sleep] {} ({}) durmiendo {}s | RAM libre: {}".format(
        NODE_ID, rol, SLEEP_MS // 1000, gc.mem_free()))
    lightsleep(SLEEP_MS)
    print("[Wake]", NODE_ID)

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
utime.sleep_ms(2000)
gc.collect()

while True:
    try:
        ciclo()
    except Exception as e:
        print("[ERROR]", e)
        ui("ERROR", str(e)[:20], ct=st7789.RED)
        # Intentar liberar memoria aunque sea en el camino de error
        try:
            _sta.active(False)
        except:
            pass
        gc.collect()
        utime.sleep_ms(3000)
