# test_i2c.py — diagnóstico de I2C en TTGO LoRa32
# Sube este como main.py temporalmente.
# Si imprime una lista de direcciones → I2C funciona, el MPU está mal cableado o no responde
# Si crashea con abort() → conflicto de pines, hay que cambiar SDA/SCL

from machine import Pin, I2C
import utime

print("=== Test I2C ===")
print("Probando bus I2C en GPIO 21 (SDA) y GPIO 22 (SCL)...")
utime.sleep_ms(500)

try:
    i2c = I2C(0, sda=Pin(21), scl=Pin(22), freq=100_000)
    print("I2C creado OK")
    utime.sleep_ms(200)

    print("Escaneando bus...")
    devices = i2c.scan()
    print("Dispositivos encontrados:", [hex(d) for d in devices])

    if 0x68 in devices or 0x69 in devices:
        print(">>> MPU detectado correctamente <<<")
    elif devices:
        print(">>> Hay dispositivos pero no es el MPU <<<")
    else:
        print(">>> Bus I2C OK pero NADA conectado <<<")
        print(">>> Verifica VCC, GND, SDA, SCL del MPU <<<")

except Exception as e:
    print("ERROR I2C:", e)

print("=== Fin test ===")
while True:
    utime.sleep_ms(5000)
