# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()
from machine import Pin  #investigar por que libreria Machine
import time

led_1 = Pin (2, Pin.OUT)

while True:
    led_1.value(1)
    time.sleep(0.5)
    led_1.value(0)
    time.sleep(1.5)
    