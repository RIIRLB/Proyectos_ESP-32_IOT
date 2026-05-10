from machine import Pin
import time

data = bytearray(b'\xf0\xf1\xf2') ## datos, arreglo de 3 columnas
def myHandler (pin): 				## bandera de interrupción
    global data
    for i in range(3):
        data[i]=0
    print (data)

## declaramos un pin que va hacer la interrupción
pin = Pin(4, Pin.IN, Pin.PULL_DOWN)
pin.irq(myHandler, Pin.IRQ_RISING)

## rutina normal principal
while True:
    pin.irq(None, Pin.IRQ_RISING)
    for i in range(3):
        data[i] = 255
    if data[0] != data[1] or data[1] != data[2] or data[2] != data[0]:
        print (data)
    pin.irq(myHandler, Pin.IRQ_RISING)