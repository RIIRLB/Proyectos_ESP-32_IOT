### Detector de potenciometro
from machine import Pin		#pines Analogicos 0, 2, 4, 12-15
from machine import ADC     #utilizando la libraria machine usamos
                            ## ADC (analog to digital conversion); pines 0, 2, 4, 12-15
from time import sleep
while True:
    adc = ADC(Pin(34))  # Crear objeto ADC en el pin 2
    val = adc.read_u16()  # Leer el valor analógico (0 a 65535)
# val = adc.read_uv()  # Si tu placa soporta lectura en microvolts

    print (val)
    sleep(1)