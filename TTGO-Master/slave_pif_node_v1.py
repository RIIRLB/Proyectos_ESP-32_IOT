# ============================================================
#  PIF_NODE v0.3 — Nodo Genérico de la Malla PIF / LAB-ARTE
#
#  Correcciones v0.3:
#   [FIX-1] Botón manual usaba parámetro inexistente color_titulo=
#           → corregido a ct=
#   [FIX-2] Relay slave→slave mejorado: los FBs de hijos se reenvían
#           con un campo "via" que evita bucles sin bloquear relays
#           legítimos de múltiples saltos.
#   [NEW]   Tabla de nodos vistos: el relay solo reenvía un FB
#           una sola vez por ciclo (dedup por id+ciclo).
#
#  Roles auto-detectados:
#    HOJA       → recibe WAVE, mide, responde al padre
#    INTERMEDIO → recibe WAVE, mide, rebroadcastea, espera hijos,
#                 reenvía sus FBs al padre, envía el propio
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
SLEEP_MS         = 30_000   # Reposo entre ciclos
VENTANA_PADRE_MS = 6_000    # Tiempo esperando WAVE del padre
                             # (aumentado para compensar fase WiFi del master)
VENTANA_HIJOS_MS = 4_000    # Tiempo esperando FEEDBACK de hijos
MAX_TTL          = 6        # Máx saltos de una WAVE
BROADCAST_MAC    = b'\xff\xff\xff\xff\xff\xff'

# ───────────────────────────────────────────────
#  PINES DE SENSORES
# ───────────────────────────────────────────────
PIN_DHT11  = 15
PIN_MQ135  = 34
PIN_SDA    = 21
PIN_SCL    = 22
MPU_ADDR   = 0x68

# ───────────────────────────────────────────────
#  HARDWARE
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

# ───────────────────────────────────────────────
#  DISPLAY — helpers
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
    tft.write(font_md, "PIF NODE",    cx(font_md, "PIF NODE"),    4,  st7789.GREEN)
    tft.write(font_sm, "LAB-ARTE",    cx(font_sm, "LAB-ARTE"),    44, st7789.CYAN)
    tft.write(font_sm, NODE_ID,       cx(font_sm, NODE_ID),       70, st7789.YELLOW)
    tft.write(font_sm, "v0.3",        cx(font_sm, "v0.3"),        96, st7789.WHITE)

def ui_sensor(t, h, mq, rol=""):
    tft.fill(st7789.BLACK)
    tft.write(font_sm, NODE_ID, cx(font_sm, NODE_ID), 4, st7789.CYAN)
    linea_th = "T:{}C  H:{}%".format(t, h)
    tft.write(font_sm, linea_th, cx(font_sm, linea_th), 30, st7789.WHITE)
    linea_mq = "MQ:{}".format(mq)
    tft.write(font_sm, linea_mq, cx(font_sm, linea_mq), 56, st7789.WHITE)
    if rol:
        tft.write(font_sm, rol, cx(font_sm, rol), 82, st7789.YELLOW)

def ui_sleep():
    tft.fill(st7789.BLACK)
    tft.write(font_sm, NODE_ID,       cx(font_sm, NODE_ID),       44, st7789.CYAN)
    tft.write(font_sm, "sleeping...", cx(font_sm, "sleeping..."), 70, st7789.WHITE)
    backlight.value(0)

# ───────────────────────────────────────────────
#  MPU6050
# ───────────────────────────────────────────────
def mpu_init():
    try:
        i2c.writeto_mem(MPU_ADDR, 0x6B, b'\x00')
        utime.sleep_ms(80)
    except:
        pass

def mpu_leer():
    try:
        raw = i2c.readfrom_mem(MPU_ADDR, 0x3B, 6)
        def s(v): return v if v < 32768 else v - 65536
        ax = round(s(raw[0]<<8|raw[1]) / 16384.0, 2)
        ay = round(s(raw[2]<<8|raw[3]) / 16384.0, 2)
        az = round(s(raw[4]<<8|raw[5]) / 16384.0, 2)
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
    med.append({"t": "Temp", "v": t})
    med.append({"t": "Hum",  "v": h})

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
#  ESP-NOW — helpers
# ───────────────────────────────────────────────
def init_espnow():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=6)
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)
    return en

def cerrar_espnow(en):
    try:
        en.active(False)
    except:
        pass

def enviar_feedback(en, mediciones, parent_id):
    """
    Envía el paquete FEEDBACK de este nodo al padre (por broadcast).
    Formato compacto < 200 bytes.
    """
    pkt = json.dumps({
        "type": "FB",
        "id"  : NODE_ID,
        "par" : parent_id,
        "pl"  : mediciones
    })
    if len(pkt) > 248:
        # Fallback: recortar mediciones si el paquete es muy grande
        pkt = json.dumps({
            "type": "FB",
            "id"  : NODE_ID,
            "par" : parent_id,
            "pl"  : mediciones[:3]   # Solo temp, hum y MQ
        })
    en.send(BROADCAST_MAC, pkt)
    print("[FB] Enviado | padre:", parent_id, "| bytes:", len(pkt))

def reenviar_fb_hijo(en, raw_msg, via_chain):
    """
    Rebroadcastea el FEEDBACK de un hijo para que llegue
    al padre aunque esté fuera de rango del hijo.

    [FIX-2] Usa campo "via" como lista de nodos por los que pasó,
    evita reenvíos duplicados sin bloquear cadenas legítimas.
    Si este nodo ya aparece en "via" → ya lo retransmitimos, ignorar.
    """
    try:
        data = json.loads(raw_msg)

        # Evitar bucle: si ya estamos en la cadena de reenvío, ignorar
        via = data.get("via", [])
        if NODE_ID in via:
            return

        via.append(NODE_ID)
        data["via"] = via

        relay = json.dumps(data)
        if len(relay) < 248:
            en.send(BROADCAST_MAC, relay)
            print("[RELAY] FB de:", data.get("id", "?"),
                  "via:", " → ".join(via))
    except Exception as e:
        print("[RELAY ERROR]", e)

# ───────────────────────────────────────────────
#  CICLO PRINCIPAL
# ───────────────────────────────────────────────
def ciclo():
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

        # Botón manual → medición sin conexión
        if btn_izq.value() == 0:
            utime.sleep_ms(50)
            if btn_izq.value() == 0:
                # [FIX-1] parámetro corregido: ct= en lugar de color_titulo=
                ui("MANUAL", "Midiendo...", NODE_ID, ct=st7789.YELLOW)
                utime.sleep_ms(300)
                med, (t, h, mq) = leer_sensores()
                ui_sensor(t, h, mq, rol="SIN CONEXION")
                utime.sleep_ms(2000)

        host, msg = en.recv(50)
        if not msg:
            continue

        print("[RX RAW]", msg)
        try:
            data = json.loads(msg.decode())

            if data.get("type") == "WAVE":
                ttl = data.get("ttl", MAX_TTL)
                if ttl <= 0:
                    continue

                target = data.get("target", "ALL")
                if target != "ALL" and target != NODE_ID:
                    continue

                parent_id     = data.get("from", "MASTER")
                wave_cmd      = data.get("cmd",  "REQ:ALL")
                wave_ttl      = ttl
                wave_recibida = True
                print("[WAVE] De:", parent_id, "| cmd:", wave_cmd, "| ttl:", ttl)
                break

        except:
            pass

    if not wave_recibida:
        ui("SIN SEÑAL", "No llego WAVE", "Reintentando...", ct=st7789.RED)
        cerrar_espnow(en)
        utime.sleep_ms(800)
        ui_sleep()
        lightsleep(SLEEP_MS)
        return

    # ── Paso 2: Medir sensores propios ─────────
    ui("MIDIENDO", NODE_ID, "padre: " + parent_id[:14], ct=st7789.YELLOW)
    mediciones, (t, h, mq) = leer_sensores()
    ui_sensor(t, h, mq)
    utime.sleep_ms(400)

    # ── Paso 3: Propagar WAVE a posibles hijos ─
    if wave_ttl > 1:
        wave_hijos = json.dumps({
            "type"  : "WAVE",
            "cmd"   : wave_cmd,
            "from"  : NODE_ID,     # Ahora somos el padre de nuestros hijos
            "target": "ALL",
            "ttl"   : wave_ttl - 1
        })
        ui("PROPAGANDO", "Onda a hijos...", "ttl:{}".format(wave_ttl - 1), ct=st7789.YELLOW)
        # 2 envíos para asegurar recepción (ESP-NOW sin ACK en broadcast)
        en.send(BROADCAST_MAC, wave_hijos)
        utime.sleep_ms(100)
        en.send(BROADCAST_MAC, wave_hijos)
        print("[WAVE] Propagada con ttl:", wave_ttl - 1)
    else:
        print("[WAVE] TTL agotado, no propagar más")

    # ── Paso 4: Escuchar FBs de hijos ──────────
    ui("ESCUCHA", "Esperando hijos...", NODE_ID, ct=st7789.CYAN)

    hijos_detectados = []      # IDs únicos de hijos que respondieron
    fb_ya_relayados  = set()   # Dedup: (id, via_chain) ya reenviados

    fin_hijos = utime.ticks_add(utime.ticks_ms(), VENTANA_HIJOS_MS)

    while utime.ticks_diff(fin_hijos, utime.ticks_ms()) > 0:
        host, msg = en.recv(20)
        if not msg:
            continue
        try:
            data    = json.loads(msg.decode())
            raw_str = msg.decode()

            if data.get("type") == "FB":
                hijo_id  = data.get("id",  "?")
                hijo_par = data.get("par", "")

                # Solo nos interesa feedback dirigido a nosotros
                if hijo_par == NODE_ID:
                    if hijo_id not in hijos_detectados:
                        hijos_detectados.append(hijo_id)
                        ui("HIJO OK", hijo_id,
                           "{} hijos".format(len(hijos_detectados)),
                           ct=st7789.GREEN)
                    # Relay hacia el padre (puede necesitar múltiples saltos)
                    clave_relay = hijo_id + str(data.get("via", []))
                    if clave_relay not in fb_ya_relayados:
                        fb_ya_relayados.add(clave_relay)
                        reenviar_fb_hijo(en, raw_str, [])

        except:
            pass

    # ── Paso 5: Reportar rol ───────────────────
    if hijos_detectados:
        rol = "NODO ({} hijos)".format(len(hijos_detectados))
    else:
        rol = "HOJA"

    ui_sensor(t, h, mq, rol=rol)
    utime.sleep_ms(500)

    # Enviar nuestro propio FEEDBACK al padre
    enviar_feedback(en, mediciones, parent_id)
    ui("FB ENVIADO", rol, "→ " + parent_id[:14], ct=st7789.GREEN)
    utime.sleep_ms(800)

    # ── Paso 6: Cerrar y dormir ────────────────
    cerrar_espnow(en)
    ui_sleep()
    print("[Sleep] {} ({}) durmiendo {}s".format(
        NODE_ID, rol, SLEEP_MS // 1000))
    lightsleep(SLEEP_MS)
    print("[Wake]", NODE_ID)

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui_bienvenida()
utime.sleep_ms(2000)

while True:
    try:
        ciclo()
    except Exception as e:
        print("[ERROR]", e)
        ui("ERROR", str(e)[:20], ct=st7789.RED)
        utime.sleep_ms(3000)
