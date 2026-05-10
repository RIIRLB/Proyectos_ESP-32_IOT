import esp32
from machine import Pin, deepsleep
from time import sleep

## Configuración inicial
wake = Pin(2, Pin.IN, Pin.PULL_UP) ########################## PULL_UP

i = 10

## Configurando el pin para despertar
esp32.wake_on_ext0(pin=wake, level=esp32.WAKEUP_ALL_LOW)##### CON ALL_DOWN

## Este código se ejecutará cuando el ESP32 despierte
print('La ESP-32 ha despertado... Holi :3')


## Código principal: la cuenta regresiva
while i > 0:
    print('Preparando para dormir en', i)
    i -= 1
    sleep(1)  ## Espera 1 segundo
    
else:
    print('Entrando en deep sleep......................')
    deepsleep()  ## duerme