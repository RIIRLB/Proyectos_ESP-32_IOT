# ============================================================
#  MASTER_TTGO v7 — PIF Mesh / LAB-ARTE
#
#  Fix clave: ESP-NOW y WiFi comparten el radio STA.
#  La transición correcta es:
#    WiFi → sta.disconnect() [radio ON, sin AP] → ESP-NOW
#    ESP-NOW → espnow.active(False) → sta.connect() → WiFi
#  NUNCA hacer sta.active(False) entre medias.
#
#  Colas:
#    cola_subida  : datos de la malla → Raspberry Pi (MQTT TX)
#    cola_bajada  : órdenes de la Raspi → malla (ESP-NOW TX)
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
WIFI_SSID     = "Arte_Tenda2.4"
WIFI_PASS     = "Lab4rt3#"
MQTT_BROKER   =	"192.168.1.146 "
#WIFI_SSID     = "Totalplay-C5AC"
#WIFI_PASS     = "C5AC642BDVePRn6Z"
#MQTT_BROKER   = "192.168.100.132"
CLIENT_ID     = "MASTER_TTGO_GATEWAY"
TOPIC_SUB     = b"comandos/mesh"
TOPIC_PUB     = b"datos/sensores"
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

SLEEP_MS      = 15_000   # Reposo entre ciclos (ms)
ESCUCHA_MS    = 3_000    # Ventana de escucha ESP-NOW (ms)
BROADCAST_N   = 3        # Repeticiones de cada onda PIF

# ───────────────────────────────────────────────
#  COLAS (buzones entre fases)
# ───────────────────────────────────────────────
cola_subida = []   # Malla → Raspberry  (strings CSV listos para publicar)
cola_bajada = []   # Raspberry → Malla  (strings JSON listos para broadcast)

# ───────────────────────────────────────────────
#  HARDWARE
# ───────────────────────────────────────────────
tft       = tft_config.config(rotation=1)
hw        = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(1)

# GPIO0 como wake desde lightsleep (botón izquierdo de la TTGO)
from machine import Pin as _Pin
import esp32 as _esp32
_esp32.wake_on_ext0(pin=_Pin(0, _Pin.IN, _Pin.PULL_UP), level=_esp32.WAKEUP_ALL_LOW)

# ───────────────────────────────────────────────
#  UI — pantalla de estado
#  Todo se dibuja en una sola pasada sin pausas
#  para evitar el efecto "línea por línea".
# ───────────────────────────────────────────────
def ui(msg, sub="", color=st7789.WHITE):
    # fill() pinta el fondo completo de una vez
    tft.fill(st7789.BLACK)
    # Los tres write() van seguidos — sin sleep entre ellos
    tft.write(font_md, "PIF MASTER", 10, 8,  st7789.GREEN)
    tft.write(font_sm, msg,          10, 55, color)
    if sub:
        tft.write(font_sm, sub,      10, 88, st7789.YELLOW)

def ui_sensor(t, h, status=""):
    """
    Pantalla de sensor propio: dibuja temperatura, humedad
    y status en una sola pasada — sin llamar mostrar_en_pantalla
    para evitar el doble refresco y el render línea a línea.
    """
    tft.fill(st7789.BLACK)
    tft.write(font_md, "PIF MASTER",        10, 5,  st7789.GREEN)
    tft.write(font_sm, "Temp: {}C".format(t), 10, 48, st7789.WHITE)
    tft.write(font_sm, "Hum:  {}%".format(h), 10, 76, st7789.WHITE)
    if status:
        tft.write(font_sm, status,           10, 104, st7789.YELLOW)

# ───────────────────────────────────────────────
#  FASE 1 — WiFi + MQTT
#  Entra: STA puede estar desconectado o apagado
#  Sale:  STA desconectado del AP, radio ON
#         (listo para ESP-NOW en la siguiente fase)
# ───────────────────────────────────────────────
def fase_wifi():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)   # Radio ON — imprescindible antes de connect()

    if not sta.isconnected():
        ui("WiFi...", WIFI_SSID)
        sta.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(20):            # Espera hasta 10 s
            if sta.isconnected(): break
            utime.sleep_ms(500)

    if not sta.isconnected():
        ui("WiFi ERROR", "Sin conexion", st7789.RED)
        utime.sleep_ms(1000)
        # Dejar radio ON de todas formas para ESP-NOW
        return False

    ui("WiFi OK", sta.ifconfig()[0], st7789.GREEN)

    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(_mqtt_cb)
        client.connect()
        client.subscribe(TOPIC_SUB)
        ui("MQTT OK", MQTT_BROKER, st7789.GREEN)

        # Recibir órdenes de la Raspi → llenan cola_bajada
        client.check_msg()

        # Vaciar cola_subida → publicar datos de la malla
        enviados = 0
        while cola_subida:
            item = cola_subida.pop(0)
            client.publish(TOPIC_PUB, item)
            enviados += 1
            print("[MQTT TX]", item)

        ui("SYNC OK", "Enviados: {}  Cola: {}".format(
            enviados, len(cola_bajada)), st7789.CYAN)
        utime.sleep_ms(600)

        client.disconnect()

    except Exception as e:
        ui("MQTT ERROR", str(e)[:22], st7789.RED)
        utime.sleep_ms(800)

    # ─── TRANSICIÓN CLAVE ───────────────────────
    # Desconectarse del AP pero DEJAR EL RADIO ON.
    # Así ESP-NOW puede usar el radio en la fase siguiente.
    sta.disconnect()
    utime.sleep_ms(200)   # Pequeña pausa para que el driver procese
    # ────────────────────────────────────────────
    return True

def _mqtt_cb(topic, msg):
    """Callback MQTT: encola la orden para la malla."""
    print("[MQTT RX]", msg)
    paquete = json.dumps({
        "type"  : "WAVE",
        "cmd"   : msg.decode().strip(),
        "origin": "MASTER"
    })
    cola_bajada.append(paquete)

# ───────────────────────────────────────────────
#  FASE 2 — ESP-NOW (malla)
#  Entra: STA activo pero SIN asociación a AP
#  Sale:  ESPNow desactivado, STA sigue activo
#         (el próximo connect() lo re-asocia al AP)
# ───────────────────────────────────────────────
def fase_malla():
    # El STA ya está activo desde fase_wifi(), ESP-NOW lo necesita así
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    # 1. Bajar órdenes a los esclavos (ondas PIF)
    while cola_bajada:
        cmd = cola_bajada.pop(0)
        ui("ONDA PIF", cmd[:24], st7789.YELLOW)
        for _ in range(BROADCAST_N):
            en.send(BROADCAST_MAC, cmd)
            utime.sleep_ms(150)
        print("[ESP-NOW TX]", cmd[:40])

    # 2. Escuchar feedback de los esclavos
    ui("ESCUCHA...", "Ventana {}ms".format(ESCUCHA_MS), st7789.CYAN)
    fin = utime.ticks_add(utime.ticks_ms(), ESCUCHA_MS)
    recibidos = 0

    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        host, msg = en.recv(10)
        if msg:
            try:
                raw = msg.decode()
                data = json.loads(raw)
                # Esperamos paquetes FEEDBACK de los esclavos
                if data.get("type") == "FEEDBACK":
                    nodo = data.get("id", "?")
                    for m in data.get("payload", []):
                        hora = "{:02d}:{:02d}:{:02d}".format(
                            *utime.localtime()[3:6])
                        csv = "{},{},{},{}".format(
                            hora, nodo, m["tipo"], m["val"])
                        cola_subida.append(csv)
                    recibidos += 1
                    ui("RECIBIDO", "Nodo: " + nodo, st7789.GREEN)
                    utime.sleep_ms(400)
                else:
                    # Cualquier otro mensaje, guardarlo crudo
                    cola_subida.append(raw[:80])
            except:
                pass   # Mensaje malformado, ignorar

    ui("FIN MALLA", "Recibidos: {}".format(recibidos), st7789.CYAN)
    utime.sleep_ms(600)

    # ─── TRANSICIÓN CLAVE ───────────────────────
    # Desactivar ESP-NOW ANTES de volver a conectar WiFi.
    # El STA sigue activo — fase_wifi() hará el connect() solo.
    en.active(False)
    utime.sleep_ms(200)
    # ────────────────────────────────────────────

# ───────────────────────────────────────────────
#  BOTONES (revisados antes de cada ciclo)
# ───────────────────────────────────────────────
def revisar_botones():
    # GPIO35 (btn_env) → forzar medición propia y REQ:ALL inmediato
    if hw.btn_env.value() == 0:
        utime.sleep_ms(50)
        if hw.btn_env.value() == 0:
            ui("BOTON ENV", "REQ:ALL manual", st7789.YELLOW)
            encolar_medicion_propia()
            cola_bajada.append(json.dumps({
                "type": "WAVE", "cmd": "REQ:ALL", "origin": "MASTER"
            }))
            utime.sleep_ms(300)

    # GPIO0 (btn_dir) → mostrar cola_subida pendiente
    if hw.btn_dir.value() == 0:
        utime.sleep_ms(50)
        if hw.btn_dir.value() == 0:
            ui("COLA SUBIDA", "{} items pend.".format(len(cola_subida)),
               st7789.CYAN)
            utime.sleep_ms(2000)

# ───────────────────────────────────────────────
#  MEDICIÓN PROPIA DEL MASTER
# ───────────────────────────────────────────────
def encolar_medicion_propia():
    """
    Lee DHT11 con hasta 3 reintentos (el sensor necesita estabilizarse
    tras lightsleep). Solo encola si la lectura es válida.
    """
    t, h = "Error", "Error"
    for intento in range(3):
        t, h = hw.leer_dht()
        if t != "Error":
            break
        ui_sensor("...", "...", status="DHT reintento {}/3".format(intento+1))
        utime.sleep_ms(1000)   # Esperar antes del siguiente intento

    hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])

    if t != "Error":
        cola_subida.append("{},{},Temperatura,{}".format(hora, CLIENT_ID, t))
        cola_subida.append("{},{},Humedad,{}".format(hora, CLIENT_ID, h))
        ui_sensor(t, h, status="Encolado OK")
    else:
        # No encolar basura — solo avisar en pantalla
        ui_sensor("Err", "Err", status="DHT11 sin respuesta")

    utime.sleep_ms(800)

# ───────────────────────────────────────────────
#  CICLO PRINCIPAL
# ───────────────────────────────────────────────
def ciclo():
    backlight.value(1)

    # Dar tiempo al DHT11 para estabilizarse tras lightsleep
    # Sin esto devuelve Error en la primera lectura tras despertar
    utime.sleep_ms(2000)

    revisar_botones()

    # 1. Medir sensor propio → cola_subida
    encolar_medicion_propia()

    # 2. WiFi + MQTT: recibir órdenes, enviar datos
    fase_wifi()

    # 3. ESP-NOW: propagar órdenes, escuchar feedback
    fase_malla()

    # 4. Dormir
    ui("DORMIDITO...", "Sig. ciclo: {}s".format(SLEEP_MS // 1000),
       st7789.CYAN)
    utime.sleep_ms(1200)
    backlight.value(0)

    print("--- LightSleep {}s ---".format(SLEEP_MS // 1000))
    machine.lightsleep(SLEEP_MS)
    print("--- Despertando ---")

# ───────────────────────────────────────────────
#  ARRANQUE
# ───────────────────────────────────────────────
ui("PIF MASTER", "LAB-ARTE  v6", st7789.GREEN)
utime.sleep_ms(2000)

while True:
    try:
        ciclo()
    except Exception as e:
        print("[ERROR ciclo]", e)
        ui("ERROR", str(e)[:22], st7789.RED)
        utime.sleep_ms(4000)
