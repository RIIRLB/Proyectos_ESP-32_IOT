import gc, network, espnow, machine, utime, json, esp32
from machine import Pin, lightsleep
from umqtt.simple import MQTTClient

# Soporte de Hardware
import tft_config
import st7789py as st7789
import comfortaa_16 as font_sm
import comfortaa_24 as font_md
from sens import Sensores

gc.collect()

# ───────────────────────────────────────────────
#  CONFIGURACIÓN
# ───────────────────────────────────────────────
WIFI_SSID      = "Arte_Tenda5"
WIFI_PASS      = "Lab4rt3#"
MQTT_BROKER    = "192.168.1.146"
CLIENT_ID      = "MASTER_TTGO_GATEWAY"
TOPIC_SUB      = b"comandos/mesh"
TOPIC_PUB      = b"datos/sensores"
BROADCAST_MAC  = b'\xff\xff\xff\xff\xff\xff'

# Tiempos
SLEEP_MS       = 15_000 # Tiempo en reposo
ESCUCHA_MALLA  = 3_000  # Ventana para oír a los esclavos

# ─── BUZONES (COLAS) ───
cola_subida   = [] # De la Malla -> Hacia la Raspberry
cola_bajada   = [] # De la Raspberry -> Hacia la Malla

# ───────────────────────────────────────────────
#  HARDWARE & PANTALLA
# ───────────────────────────────────────────────
tft = tft_config.config(rotation=1)
hw = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(1)

esp32.wake_on_ext0(pin=Pin(0), level=esp32.WAKEUP_ALL_LOW)

def ui(msg, sub="", color=st7789.WHITE):
    tft.fill(st7789.BLACK)
    tft.write(font_md, "PIF MASTER V5", 10, 10, st7789.GREEN)
    tft.write(font_sm, msg, 10, 60, color)
    if sub: tft.write(font_sm, sub, 10, 90, st7789.YELLOW)

# ───────────────────────────────────────────────
#  GESTIÓN DE RED (WIFI + MQTT)
# ───────────────────────────────────────────────
def mqtt_callback(topic, msg):
    # Cuando llega algo de la Raspi, lo encolamos para la malla
    print("[MQTT RX]", msg)
    paquete = json.dumps({"type": "WAVE", "cmd": msg.decode(), "origin": "MASTER"})
    cola_bajada.append(paquete)

def conectar_y_sincronizar():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        sta.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(20):
            if sta.isconnected(): break
            utime.sleep_ms(500)
    
    if sta.isconnected():
        try:
            client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
            client.set_callback(mqtt_callback)
            client.connect()
            client.subscribe(TOPIC_SUB)
            
            # 1. Revisar si hay órdenes nuevas de la Raspi (llena cola_bajada)
            client.check_msg()
            
            # 2. Vaciar buzón de subida (Hacia la Raspi)
            while len(cola_subida) > 0:
                item = cola_subida.pop(0)
                client.publish(TOPIC_PUB, item)
                print("[MQTT TX] Enviado a Raspi")
            
            client.disconnect()
            return True
        except Exception as e:
            print("Error MQTT:", e)
    return False

# ───────────────────────────────────────────────
#  GESTIÓN DE MALLA (ESP-NOW)
# ───────────────────────────────────────────────
def operar_malla():
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)

    # 1. Bajar comandos a la malla (Ondas PIF)
    while len(cola_bajada) > 0:
        cmd = cola_bajada.pop(0)
        for _ in range(2): # Doble envío para asegurar
            en.send(BROADCAST_MAC, cmd)
            utime.sleep_ms(100)
        print("[ESP-NOW TX] Onda enviada")

    # 2. Escuchar reportes de esclavos (Feedback)
    ui("ESCUCHANDO...", "Ventana RX abierta", st7789.CYAN)
    fin = utime.ticks_add(utime.ticks_ms(), ESCUCHA_MALLA)
    
    while utime.ticks_diff(fin, utime.ticks_ms()) > 0:
        host, msg = en.recv(10)
        if msg:
            # Encolamos lo que diga la malla para el próximo ciclo WiFi
            try:
                raw = msg.decode()
                cola_subida.append(raw)
                print("[ESP-NOW RX] Guardado en cola")
                ui("DATO RECIBIDO", raw[:20], st7789.GREEN)
                utime.sleep_ms(500)
            except: pass

    en.active(False)

# ───────────────────────────────────────────────
#  BUCLE PRINCIPAL (ESTADOS)
# ───────────────────────────────────────────────
while True:
    try:
        backlight.value(1)
        
        # ESTADO 1: MEDICIÓN PROPIA
        t, h = hw.leer_dht()
        hora = "{:02d}:{:02d}".format(*utime.localtime()[3:5])
        cola_subida.append(f"{hora},{CLIENT_ID},Clima,{t}T-{h}H")
        
        # ESTADO 2: TRABAJO CON RASPBERRY (WIFI ON)
        ui("CONECTANDO...", "Sincronizando colas")
        conectar_y_sincronizar()
        
        # ESTADO 3: TRABAJO CON MALLA (ESP-NOW ON)
        operar_malla()
        
        # ESTADO 4: LIMPIEZA Y REPOSO
        ui("DORMIDITO...", f"Sig. ciclo: {SLEEP_MS//1000}s")
        utime.sleep_ms(1500)
        
        # Apagado total de radios para evitar "Internal Error"
        network.WLAN(network.STA_IF).active(False)
        backlight.value(0)
        
        print("--- Entrando en LightSleep ---")
        machine.lightsleep(SLEEP_MS)
        
    except Exception as e:
        print("Falla en el loop:", e)
        utime.sleep_ms(5000)