import network
import urequests
import time
import dht
from machine import Pin

# librerías de pantalla
import tft_config
import st7789py as st7789
import comfortaa_24 as font

# ——— Configuración del display ———
tft = tft_config.config(rotation=1)
#tft.init()
tft.fill(st7789.BLACK)

# WiFi
SSID     = "Arte_Tenda2.4"
PASSWORD = "Lab4rt3#"
SERVER_URL = "http://192.168.1.25:5000//data"

# Inicializa DHT11 y botón
sensor = dht.DHT11(Pin(15))
boton = Pin(33, Pin.IN, Pin.PULL_UP)  # Botón en GPIO33

# Conexión WiFi
sta = network.WLAN(network.STA_IF)
sta.active(True)
sta.connect(SSID, PASSWORD)

while not sta.isconnected():
    print(".", end="")
    time.sleep(0.5)

print("\n WiFi conectado:", sta.ifconfig())

# Enviar datos al servidor Flask
def enviar_datos():
    try:
        sensor.measure()
        temp = sensor.temperature()
        hum  = sensor.humidity()
        tft.fill(st7789.BLACK)
        tft.write(font, "DATOS ENVIADOS", 10, 50, st7789.GREEN,  st7789.BLACK)
        payload = {
            "id": "TTGO1",
            "value": f"Temp:{temp}C     Hum:{hum}%"
        }
    except OSError:
        tft.fill(st7789.BLACK)
        tft.write(font, "SENSOR NO CONECTADO",   10,  40, st7789.RED,   st7789.BLACK)
        payload = {
            "id": "TTGO1",
            "value": "Sensor no conectado"
        }

    try:
        res = urequests.post(SERVER_URL, json=payload)
        print("HTTP", res.status_code, res.text)
        res.close()
    except Exception as e:
        print(" Error al enviar:", e)
        tft.fill(st7789.BLACK)
        tft.write(font, "ERROR AL ENVIAR",   0,  40, st7789.RED,   st7789.BLACK)
        tft.write(font, "SERVIDOR NO ENCONTRADO",   0,  80, st7789.RED,   st7789.BLACK)

# Estado previo del botón
estado_anterior = boton.value()

# Loop principal
while True:
    estado_actual = boton.value()

    if estado_anterior == 1 and estado_actual == 0:
        tft.write(font, "Boton presionado", 10, 10, st7789.CYAN,  st7789.BLACK)
        tft.write(font, "Enviando datos ...", 10, 40, st7789.CYAN,  st7789.BLACK)
        print(" Botón presionado, enviando datos...")
        enviar_datos()
        time.sleep(0.3)  # pequeño rebote
        time.sleep(2)
        tft.fill(st7789.BLACK)

    estado_anterior = estado_actual
    time.sleep(0.05)
