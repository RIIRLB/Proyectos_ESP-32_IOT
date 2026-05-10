## Deepsleep + DHT11
import esp32
from machine import Pin, deepsleep
from time import sleep
import dht

wake_button = Pin(4, Pin.IN, Pin.PULL_UP)       ## Botón para despertar

## Configuración del pin para despertar mientras esta en DeepSleep
esp32.wake_on_ext0(pin=wake_button, level=esp32.WAKEUP_ALL_LOW)

## inicializamos el sensor DHT11
sensor = dht.DHT11(Pin(15))

## Para que la ESP sepa que hora es
epoch_time = utime.time()
local_time = utime.localtime(epoch_time)

##------------>   Inicio del programa principal
print("La ESP-32 ha despertado... Holi :3")

## da la hora
print("ESP32 despertó en: {:02d}:{:02d}:{:02d}".format(local_time[3], local_time[4], local_time[5]))

## hace la medición
try:        ### sentencia para el manejo de errores en algun proceso de 
            ### el bloque de codigo que se este ejecutando try-except-finaly
    sensor.measure()    ### se toma una lectura del sensor
        
    temp = sensor.temperature() ###se obtiene temprarutra
    hum = sensor.humidity()     ###se obtiene humedad
        
    ### imprime los valores en °C y la humedad en %
    print("Temperatura: {}°C   Humedad: {}%".format(temp, hum))
        
### si es que no esta bien conectado, se mandara el mensaje
except OSError as e: 
    print("Error al leer el sensor, conectalo bien!:", e)


# Mensaje antes de entrar en deepsleep indefinido
print("medicion completada, entrando en deep sleep")
deepsleep()