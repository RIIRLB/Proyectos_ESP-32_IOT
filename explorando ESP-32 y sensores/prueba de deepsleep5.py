from machine import Pin, deepsleep, RTC
import esp32
import time

## configurando el pin del LED
led = Pin(2, Pin.OUT)
led.value(0)  ## apagado inicialmente

## configurando el botón
button = Pin(4, Pin.IN, Pin.PULL_UP)  ##(pin,datos de entrada, pull up interno)

## se configura el RTC
rtc = RTC()

## función para entrar en deepsleep
def enter_deep_sleep():
    esp32.wake_on_ext1(pins=[button], level=esp32.WAKEUP_ALL_LOW) ## se usa EXT1
    print("Entrando en deep sleep...")
    rtc.memory(b'LED_ON')  										## guarda el estado en la memoria RTC
    time.sleep(1)  												## espera un momento antes de entrar en deep sleep
    deepsleep()

## se verifica si hay datos en la memoria RTC
if rtc.memory() == b'LED_ON':
    led.value(1)  						## como tiene un estado guardado es 'LED_ON' enciende el led
    rtc.memory(b'')  					## limpia la memoria RTC, para volver a deepsleep  

## entra en deep sleep si el botón no está presionado
if button.value() == 1:
    enter_deep_sleep()
print("ESP32 ha despertado")  ## mensaje de que esta despierta

## bucle principal
while True:
    if button.value() == 0:  ## si el botón está presionado
        led.value(1)  ## enciende el LED
        break  ## sale del bucle una vez que el botón es presionado
