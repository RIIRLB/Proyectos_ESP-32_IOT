import machine
import esp32
import time

# Pines táctiles
TOUCH_PIN = 4  # Cambia este pin según tu configuración

# Pines de los LEDs
LED1 = machine.Pin(2, machine.Pin.OUT)
LED2 = machine.Pin(4, machine.Pin.OUT)
LED3 = machine.Pin(16, machine.Pin.OUT)

# Función para encender los LEDs
def wake_up():
    LED1.on()
    LED2.on()
    LED3.on()
    time.sleep(5)  # Mantener los LEDs encendidos por 5 segundos
    LED1.off()
    LED2.off()
    LED3.off()

# Configuración del pin táctil para despertar el ESP32
touch = esp32.TouchPad(machine.Pin(TOUCH_PIN))
touch.config(400)  # Umbral de detección de toque

# Despierta el ESP32 con el pin táctil
esp32.wake_on_touch(True)

# Entra en modo de sueño profundo
while True:
    if touch.read() < 400:  # Si se detecta un toque
        wake_up()
    machine.deepsleep()  # Entra en modo de sueño profundo
