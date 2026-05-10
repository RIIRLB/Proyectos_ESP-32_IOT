# Codigo de Ana, para hacer que funcione el "termometro"
class MLX90614:
    def __init__(self, i2c, address=0x5A):
        self.i2c = i2c              # Guarda el bus I2C
        self.address = address      # Dirección del sensor en el bus

    def read_ambient_temp(self):
        return self._read_temp(0x06)

    def read_object_temp(self):
        return self._read_temp(0x07)

    def _read_temp(self, register):

        # Lee 2 bytes del registro especificado
        # readfrom_mem(direccion, registro, numero_bytes)
        data = self.i2c.readfrom_mem(self.address, register, 2)
        
        # El sensor devuelve 2 bytes en formato little-endian:
        # data[0] → byte bajo
        # data[1] → byte alto
                # Se combinan los dos bytes en un solo valor de 16 bits
        temp = (data[1] << 8) | data[0]
        temp = (temp * 0.02) - 273.15 # Convertir de Kelvin a Celsius
        return temp