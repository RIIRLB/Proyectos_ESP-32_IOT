# Deepsleep + DHT11 + RTC + Wake-button + Display TTGO
# by RILB -06-Jun-25
import esp32
from machine import Pin, RTC, deepsleep
from time import sleep, localtime
import dht

# librerías de pantalla
import tft_config
import st7789py as st7789
import comfortaa_24 as font

# ——— Configuración del display ———
tft = tft_config.config(rotation=1)
#tft.init()
tft.fill(st7789.BLACK)

# ——— Wake‑on‑button en GPIO33 ———
btn = Pin(33, Pin.IN, Pin.PULL_UP)
esp32.wake_on_ext0(pin=btn, level=esp32.WAKEUP_ALL_LOW)

# ——— Sensor DHT11 en GPIO15 ———
sensor_present = True
try:
    sensor = dht.DHT11(Pin(15))
except Exception:
    sensor_present = False

# ——— RTC para “STOP” ———
rtc = RTC()
try:
    stop_flag = rtc.memory().decode() == "STOP"
except:
    stop_flag = False

if stop_flag:
    tft.fill(st7789.BLACK)
    tft.write(font, "  DETENIDO  ",   10,  40, st7789.RED,   st7789.BLACK)
    tft.write(font, "Reinicia disp.", 10,  80, st7789.WHITE, st7789.BLACK)
    while True:
        pass

try:
    # ——— Obtén hora actual ———
    yy, mm, dd, hh, mi, ss, *_ = localtime()

    # ——— Mide sensor si existe ———
    temp = hum = None
    if sensor_present:
        try:
            sensor.measure()
            temp = sensor.temperature()
            hum  = sensor.humidity()
        except OSError:
            sensor_present = False

    # ——— Muestra en pantalla ———
    tft.fill(st7789.BLACK)

    # Cabecera y hora
    tft.write(font, "Mi ESP32 TTGO", 10, 10, st7789.CYAN,  st7789.BLACK)
    hora_str = "{:02d}:{:02d}:{:02d}".format(hh, mi, ss)
    tft.write(font, "Hora: " + hora_str, 10, 60, st7789.WHITE, st7789.BLACK)
    print("Hora: " + hora_str)
    sleep(1)
    # Sensor
    if not sensor_present:
        tft.fill(st7789.BLACK)
        tft.write(font, "Sensor no conectado", 10, 40, st7789.RED, st7789.BLACK)
        print("no se detecta nada")
    else:
        tft.fill(st7789.BLACK)
        tft.write(font, f"Temp: {temp}°C",      10, 20, st7789.YELLOW, st7789.BLACK)
        tft.write(font, f"Humedad: {hum}%",     10, 60, st7789.GREEN,  st7789.BLACK)
        print("Temp: {temp} C")
        print("Humedad: {hum}%")

    # ——— Espera antes de dormir ———
    sleep(5)
    deepsleep()

except KeyboardInterrupt:
    rtc.memory("STOP")
    tft.fill(st7789.BLACK)
    tft.write(font, "  DETENIDO  ",   10,  40, st7789.RED,   st7789.BLACK)
    tft.write(font, "Reinicia dispositivo", 10,  80, st7789.WHITE, st7789.BLACK)
    while True:
        pass
 