## DeepSleep + Interrupcion
import esp32
from machine import Pin, deepsleep
from time import sleep

## Configuración de los botones
interrupt_button = Pin(2, Pin.IN, Pin.PULL_UP)  ## Botón para entrar en deep sleep indefinido
wake_button = Pin(4, Pin.IN, Pin.PULL_UP)       ## Botón para despertar

## Configuración del pin para despertar mientras esta en DeepSleep
esp32.wake_on_ext0(pin=wake_button, level=esp32.WAKEUP_ALL_LOW)

## Función de interrupción: entra en deep sleep indefinido
def enter_deep_sleep(pin):
    print("Interrupción activada. Entrando en deep sleep indefinido............")
    deepsleep()  ## Deep sleep indefinido

## Configurar la interrupción en el botón de deep sleep
interrupt_button.irq(trigger=Pin.IRQ_RISING, handler=enter_deep_sleep)

## Inicio del programa
print("La ESP-32 ha despertado... Holi :3")

## Contador descendente
for i in range(10, 0, -1):
    print("Contando: ",i)
    sleep(1)  ## Espera 1 segundo entre números

# Mensaje antes de entrar en deep sleep indefinido
print("La cuenta terminó. Entrando en deep sleep de 5 segundos")
deepsleep(5000)