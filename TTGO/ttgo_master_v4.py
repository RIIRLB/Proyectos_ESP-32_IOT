import network
import time
from machine import Pin, lightsleep, esp32
from umqtt.simple import MQTTClient
from sens import Sensores
import tft_config
import st7789py as st7789

# CONFIGURACIÓN
WIFI_SSID = "La_Red_WiFi"			# Nombre de la Red Wifi
WIFI_PASS = "El_Password"			# Contraseña de red
MQTT_BROKER = "IP_DE_LA_RASPBERRY" 	# Ej: "192.168.1.50"
CLIENT_ID = "TTGO_1"

tft = tft_config.config(rotation=1)
hw = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)

# Boton 33 - despierta!
esp32.wake_on_ext0(pin=Pin(33, Pin.IN, Pin.PULL_UP), level=esp32.WAKEUP_ALL_LOW)

def conectar_wifi():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        sta.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(100): 			#espremaos hasta 10 seg
            if sta.isconnected(): break
            time.sleep(0.1)
    return sta.isconnected()

def subir_a_servidor(raw_msg):
    """
    Ahora envía los datos recibidos de los esclavos (PIF) a la Raspberry Pi
    """
    if conectar_wifi():
        try:
            client = MQTTClient(CLIENT_ID, MQTT_BROKER)
            client.connect()
            # Publicamos en el tema "sensores/red"
            client.publish("sensores/red", raw_msg)
            client.disconnect()
            print(">>> [MQTT]: Enviado con éxito")
        except Exception as e:
            print("Error MQTT:", e)

# MAIN LOOP
while True:
    # 1 Al despertar, encender pantalla y mostrar sensor actual
    hw.tft.fill(st7789.BLACK)
    modo = "DHT11" 
    hw.mostrar_en_pantalla(modo, status="DESPIERTO")
    
    # 2 Si se presiona el botón de envío (Pin 35)
    if hw.btn_env.value() == 0:
        hw.mostrar_en_pantalla(modo, status="CONECTANDO...")
        # Obtener lectura según el modo
        t, h = hw.leer_dht()
        payload = "temp={},hum={}".format(t, h)
        
        if subir_a_servidor(payload):
            hw.mostrar_en_pantalla(modo, status="ENVIADO MQTT!")
        else:
            hw.mostrar_en_pantalla(modo, status="ERROR WIFI/MQTT")
        
        time.sleep(2) # Tiempo para leer la pantalla

    # 3 Ir a dormir (Light Sleep)
    # Apagamos luz de fondo para ahorrar
    Pin(4, Pin.OUT).value(0) 
    print("Entrando en Light Sleep...")
    lightsleep(30000) # Dormir 30 seg o hasta presionar botón 33
    Pin(4, Pin.OUT).value(1) # Encender al despertar
