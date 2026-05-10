import gc
import network
import espnow
import machine
import utime
import json
import esp32
from machine import Pin, lightsleep
from umqtt.simple import MQTTClient

# Soporte de Hardware y Pantalla
import tft_config
import st7789py as st7789
import comfortaa_16 as font_sm
import comfortaa_24 as font_md
from sens import Sensores

gc.collect()

# ───────────────────────────────────────────────
#  CONFIGURACIÓN FINAL
# ───────────────────────────────────────────────
WIFI_SSID      = "Arte_Tenda5"
WIFI_PASS      = "Lab4rt3#"
MQTT_BROKER    = "192.168.1.146"
CLIENT_ID      = "MASTER_TTGO_GATEWAY"

TOPIC_SUB      = b"comandos/mesh"
TOPIC_PUB      = b"datos/sensores"
BROADCAST_MAC  = b'\xff\xff\xff\xff\xff\xff'

SLEEP_MS       = 20_000  # Tiempo en LightSleep (20 seg)
VENTANA_RX_MS  = 4_000   # Tiempo esperando respuesta de la malla (4 seg)

# ───────────────────────────────────────────────
#  INICIALIZACIÓN
# ───────────────────────────────────────────────
tft = tft_config.config(rotation=1)
hw = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)
backlight = Pin(4, Pin.OUT)
backlight.value(1)

# Configurar despertar por botón (GPIO 0)
esp32.wake_on_ext0(pin=Pin(0), level=esp32.WAKEUP_ALL_LOW)

def mostrar_info(linea1, linea2="", color=st7789.WHITE):
    tft.fill(st7789.BLACK)
    tft.write(font_md, "PIF MASTER", 10, 10, st7789.GREEN)
    tft.write(font_sm, linea1, 10, 60, color)
    if linea2:
        tft.write(font_sm, linea2, 10, 90, st7789.YELLOW)

# ───────────────────────────────────────────────
#  CONEXIÓN WIFI Y MQTT
# ───────────────────────────────────────────────
def conectar_servidor():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        mostrar_info("WIFI...", "Conectando")
        sta.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(20):
            if sta.isconnected(): break
            utime.sleep_ms(500)
    
    if sta.isconnected():
        try:
            c = MQTTClient(CLIENT_ID, MQTT_BROKER, port=1883, keepalive=60)
            c.connect()
            return c
        except:
            return None
    return None

# ───────────────────────────────────────────────
#  PROCESO PRINCIPAL (CICLO PIF)
# ───────────────────────────────────────────────
def ejecutar_pif():
    backlight.value(1) # Encender pantalla al despertar
    
    # 1. CONECTAR
    client = conectar_servidor()
    if not client:
        mostrar_info("ERROR", "Servidor Offline", st7789.RED)
        utime.sleep_ms(2000)
        return

    # 2. MEDICIÓN (Humedad y Temperatura)
    t, h = hw.leer_dht()
    mostrar_info(f"T: {t}C  H: {h}%", "TX -> RASPI", st7789.CYAN)
    
    # 3. ENVÍO A RASPBERRY
    hora = "{:02d}:{:02d}:{:02d}".format(*utime.localtime()[3:6])
    payload = f"{hora},{CLIENT_ID},Clima,{t}T-{h}H"
    try:
        client.publish(TOPIC_PUB, payload)
        utime.sleep_ms(500)
    except:
        pass

    # 4. RECEPCIÓN (Escuchar malla ESP-NOW)
    en = espnow.ESPNow()
    en.active(True)
    en.add_peer(BROADCAST_MAC)
    
    mostrar_info("RX MESH...", "Esperando nodos", st7789.BLUE)
    
    # Ventana de tiempo con utime.ticks
    fin_ventana = utime.ticks_add(utime.ticks_ms(), VENTANA_RX_MS)
    while utime.ticks_diff(fin_ventana, utime.ticks_ms()) > 0:
        host, msg = en.recv(10)
        if msg:
            try:
                data = json.loads(msg.decode())
                if data.get("type") == "FEEDBACK":
                    nodo_id = data.get("id", "??")
                    mostrar_info(f"NODO: {nodo_id}", "DATO RECIBIDO", st7789.GREEN)
                    # Reenviar dato del nodo a la Raspi
                    for m in data.get("payload", []):
                        p = f"{hora},{nodo_id},{m['tipo']},{m['val']}"
                        client.publish(TOPIC_PUB, p)
                    utime.sleep_ms(1000)
            except:
                pass

    en.active(False)
    client.disconnect()

    # 5. LIGHT SLEEP
    mostrar_info("DORMIDITO...", f"Wait {SLEEP_MS//1000}s", st7789.WHITE)
    utime.sleep_ms(1500) # Buffer para leer pantalla
    
    backlight.value(0) # Apagar pantalla para ahorrar
    print("Entrando en LightSleep...")
    machine.lightsleep(SLEEP_MS)
    print("Despertando...")

# ───────────────────────────────────────────────
#  BUCLE INFINITO
# ───────────────────────────────────────────────
while True:
    try:
        ejecutar_pif()
    except Exception as e:
        print("Error en ciclo:", e)
        utime.sleep_ms(5000)