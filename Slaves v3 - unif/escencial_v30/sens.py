# sens.py — STUB (se implementa en el PASO 3)
#
# Basado en sensores.py de Ana (autodetección por hardware + recuperación
# de bus I2C) + nuestros nombres de medición y umbrales (sección 3.3).
# Mapa de medición acordado: Temp/Hum (DHT11), Temp_obj/Temp_amb (MLX90614),
# AccX/AccY/AccZ (MPU6050), MQ135 (ADC).

class Sensores:
    def __init__(self, pin_dht=15, pin_mq=33, scl=22, sda=21,
                 usar_dht="auto", usar_mq="auto",
                 usar_mpu="auto", usar_gy="auto"):
        self.activos = []
        # TODO paso 3: portar detección por pull-up/pull-down y escaneo I2C

    def detectar(self):
        self.activos = []
        return self.activos     # TODO paso 3

    def leer(self):
        # Devuelve (med, lec): med = lista [{"t":..,"v":..}], lec = dict resumen
        return [], {}           # TODO paso 3
