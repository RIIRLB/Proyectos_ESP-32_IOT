# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()
from machine import Pin  #investigar por que libreria Machine
import time

led_1 = Pin (2, Pin.OUT)

while True:
    i = 0
    while i < 1:
        led_1.value(1)
        time.sleep(i)
        led_1.value(0)
        time.sleep(i)
        i += 0.1  # Incremento decimal
    
    j = 1
    while j > 0:
        led_1.value(1)
        time.sleep(j)
        led_1.value(0)
        time.sleep(j)
        j -= 0.1  # Decremento decimal
    break

    ##led_1.value(1)
    ##time.sleep(0.2)
    ##led_1.value(0)
    ##time.sleep(0.2)
    ##led_1.value(1)
    ##time.sleep(0.2)
    ##led_1.value(0)
    ##time.sleep(0.2)
    