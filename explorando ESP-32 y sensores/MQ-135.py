### microproyecto #4.2 MQ-135
import math
import time
from machine import ADC, Pin

class MQ135:
    RLOAD = 10.0
    RZERO = 76.63
    PARA = 116.6020682
    PARB = 2.769034857
    CORA = 0.00035
    CORB = 0.02718
    CORC = 1.39538
    CORD = 0.0018
    CORE = -0.003333333
    CORF = -0.001923077
    CORG = 1.130128205
    ATMOCO2 = 397.13

    def __init__(self, pin):
        self.adc = ADC(Pin(pin))
        self.adc.width(ADC.WIDTH_12BIT)
        self.adc.atten(ADC.ATTN_11DB)

    def get_resistance(self):
        value = self.adc.read()
        if value == 0:
            return -1
        return (4095.0 / value - 1.0) * self.RLOAD

    def get_corrected_resistance(self, temperature, humidity):
        return self.get_resistance() / self.get_correction_factor(temperature, humidity)

    def get_correction_factor(self, temperature, humidity):
        if temperature < 20:
            return self.CORA * temperature * temperature - self.CORB * temperature + self.CORC - (humidity - 33.) * self.CORD
        return self.CORE * temperature + self.CORF * humidity + self.CORG

    def get_ppm(self):
        return self.PARA * math.pow((self.get_resistance() / self.RZERO), -self.PARB)

    def get_corrected_ppm(self, temperature, humidity):
        return self.PARA * math.pow((self.get_corrected_resistance(temperature, humidity) / self.RZERO), -self.PARB)

    def get_rzero(self):
        return self.get_resistance() * math.pow((self.ATMOCO2 / self.PARA), (1.0 / self.PARB))

    def get_corrected_rzero(self, temperature, humidity):
        return self.get_corrected_resistance(temperature, humidity) * math.pow((self.ATMOCO2 / self.PARA), (1.0 / self.PARB))

# Ejemplo de uso
mq135 = MQ135(34)  # Pin 34

while True:
    temperature = 25.0  # Temperatura ambiente en grados Celsius
    humidity = 50.0     # Humedad relativa en porcentaje

    resistance = mq135.get_resistance()
    ppm = mq135.get_ppm()
    corrected_ppm = mq135.get_corrected_ppm(temperature, humidity)
    
    print("----------------------------------------------")
    print("Resistencia: {:.2f} kOhms".format(resistance))
    print("PPM = Partes Por Millon")
    print("PPM: {:.2f}".format(ppm))
    print("PPM corregido: {:.2f}".format(corrected_ppm))
    print("----------------------------------------------")

    time.sleep(1)