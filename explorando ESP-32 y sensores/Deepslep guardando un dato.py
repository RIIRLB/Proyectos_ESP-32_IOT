## Deepsleep guardando un dato
import esp32
from machine import Pin, deepsleep, RTC
from time import sleep

## Configuración inicial
wake = Pin(14, Pin.IN, Pin.PULL_UP)
rtc = RTC()

## Verifica si hay datos en la memoria RTC
i = rtc.memory()
if i:
    i = int(i)  ## Recupera el valor guardado
else:
    i = 10  ## Valor inicial si no hay datos

## Este código se ejecutará cuando el ESP32 despierte
print('La ESP-32 ha despertado... Holi :3')

## Configura el pin para despertar
esp32.wake_on_ext0(pin=wake, level=esp32.WAKEUP_ALL_LOW)

## tarea principal
if i > 0:
    print('nos quedamos en', i)
    i -= 1
    rtc.memory(str(i).encode())  ## Guarda el valor actualizado en memoria RTC
    sleep(1)  ## Espera 1 segundo
    print('Entrando en deep sleep........')
    deepsleep(5000)  ## Duerme por 5 segundos
else:
    print('Contador terminado, no se volverá a dormir.')