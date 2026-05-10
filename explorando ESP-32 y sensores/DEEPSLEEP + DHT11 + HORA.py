## Deepsleep + DHT11 + RTC para obtener la hora
import esp32
from machine import Pin, deepsleep, RTC
from time import sleep, localtime, mktime
import dht

## Botón para despertar
wake_button = Pin(4, Pin.IN, Pin.PULL_UP)

## Configuración del pin para despertar desde deepsleep
esp32.wake_on_ext0(pin=wake_button, level=esp32.WAKEUP_ALL_LOW)

## Inicializamos el sensor DHT11
sensor = dht.DHT11(Pin(15))

# RTC para mantener el tiempo entre deep sleep
rtc = RTC()

# Configuración inicial del RTC si es la primera vez
if not rtc.datetime()[0]:  # Si el RTC no tiene tiempo configurado
    # Configura la hora inicial (año, mes, día, hora, minuto, segundo, día de la semana, subsegundos)
    rtc.datetime((2024, 12, 16, 1, 0, 0, 0, 0))  # Inicializa el RTC con una fecha/hora específica

# Recupera la hora actual
current_time = localtime()  # Devuelve la hora en formato (año, mes, día, hora, minuto, segundo, día_semana, día_año)

# Imprime la hora de despertar
print("----------------------------------------------------")
print("La ESP-32 ha despertado... Holi :3")
print("Hora actual: {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
    current_time[0], current_time[1], current_time[2],  # Año, mes, día
    current_time[3], current_time[4], current_time[5]   # Hora, minuto, segundo
))

# Toma una medición del sensor DHT11
try:
    sensor.measure()
    temp = sensor.temperature()
    hum = sensor.humidity()

    print("Temperatura: {}°C   Humedad: {}%".format(temp, hum))

except OSError as e:
    print("Error al leer el sensor, conectalo bien!:", e)

# Actualiza la hora sumando el tiempo de deep sleep (si es necesario)
# Aquí no sumamos tiempo porque es indefinido, pero si tienes un tiempo fijo puedes hacerlo.

# Mensaje antes de entrar en deep sleep indefinido
print("Medición completada. Entrando en deepsleep...")
print("----------------------------------------------------")
deepsleep()
