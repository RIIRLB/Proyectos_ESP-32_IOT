## Deepsleep + DHT11 + RTC + Ctrl+C + Display OLED
# RILB
import esp32
from machine import Pin, deepsleep, RTC
from time import sleep, localtime
import dht
from oled import Display

# Para que se pueda imprimir los mensajes en el dislpay
display = Display()

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

# Muestra texto, se agrega un retardo de 2 seg para que pueda leer el usuario
display.text("La ESP-32 ha despertado... Holi :3")
sleep(2)
display.text(("Hora actual: {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                current_time[0], current_time[1], current_time[2],  
                current_time[3], current_time[4], current_time[5]   
                )))
sleep(3)

display.show()

# Toma una medición del sensor DHT11
try:
    sensor.measure()
    temp = sensor.temperature()
    hum = sensor.humidity()
    
    display.text("Temperatura: {}°C \nHumedad: {}%".format(temp, hum))
    sleep(3)

except OSError as e:
    display.text("Error al leer el sensor: " + str(e))


# Esperar unos segundos antes de dormir y permitir interrupción
try:
    print("Presiona Ctrl+C ahora para detener la ejecución y volver a la consola...")
    sleep(5)  # Espera antes de entrar en deep sleep

    display.text("Medición completada. Entrando en Deepsleep...")
    print("----------------------------------------------------")
    sleep(2)
    display.clear() 		# Apagamos el display para no gastar energia
    deepsleep()

except KeyboardInterrupt:
    print("Interrupción detectada. Programa detenido.")
    rtc.memory("STOP")  # Guarda el estado en la memoria RTC
    while True:
        pass  # Se queda en un bucle infinito esperando reinicio manual

