## Deepsleep + DHT11 + RTC + Ctrl+C
import esp32
from machine import Pin, deepsleep, RTC
from time import sleep, localtime
import dht

## Botón para despertar
wake_button = Pin(4, Pin.IN, Pin.PULL_UP)

## Configuración del pin para despertar desde deepsleep
esp32.wake_on_ext0(pin=wake_button, level=esp32.WAKEUP_ALL_LOW)

## Inicializamos el sensor DHT11
sensor = dht.DHT11(Pin(15))

# RTC para almacenar si se debe detener el código
rtc = RTC()
try:
    stop_flag = rtc.memory().decode() == "STOP"
except:
    stop_flag = False

if stop_flag:
    print("Ejecución detenida. Reinicia manualmente para continuar.")
    while True:
        pass  # Se queda en un bucle infinito esperando que reinicies manualmente

# Recupera la hora actual
current_time = localtime()

print("----------------------------------------------------")
print("La ESP-32 ha despertado... Holi :3")
print("Hora actual: {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
    current_time[0], current_time[1], current_time[2],  
    current_time[3], current_time[4], current_time[5]   
))

# Toma una medición del sensor DHT11
try:
    sensor.measure()
    temp = sensor.temperature()
    hum = sensor.humidity()

    print("Temperatura: {}°C   Humedad: {}%".format(temp, hum))

except OSError as e:
    print("Error al leer el sensor, conéctalo bien!:", e)

# Esperar unos segundos antes de dormir y permitir interrupción
try:
    print("Presiona Ctrl+C ahora para detener la ejecución y volver a la consola...")
    sleep(5)  # Espera antes de entrar en deep sleep

    print("Medición completada. Entrando en deepsleep...")
    print("----------------------------------------------------")
    deepsleep()

except KeyboardInterrupt:
    print("Interrupción detectada. Programa detenido.")
    rtc.memory("STOP")  # Guarda el estado en la memoria RTC
    while True:
        pass  # Se queda en un bucle infinito esperando reinicio manual

