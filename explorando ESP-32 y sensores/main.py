## Código fusilado de un tipo que hizo un conador con deepsleep 
from machine import Pin, deepsleep, RTC
import esp32
import time

# Configuración inicial
wakeup_pin = Pin(2, Pin.IN, Pin.PULL_UP)  # GPIO 2 como entrada con PULL_UP
rtc = RTC()

# Leer o inicializar el contador de reinicios en memoria RTC
boot_count = rtc.memory()
if boot_count:
    boot_count = int(boot_count.decode()) + 1
else:
    boot_count = 1

# Mostrar el número de reinicios
print(f"Boot number: {boot_count}")

# Guardar el contador actualizado en la memoria RTC
rtc.memory(str(boot_count).encode())

# Configurar el pin para despertar del deep sleep
esp32.wake_on_ext0(pin=wakeup_pin, level=esp32.WAKEUP_ALL_LOW)

# Agregar un retardo de 1 segundo para evitar activaciones múltiples
time.sleep(1)

# Mensaje antes de entrar en deep sleep
print("I'm going to sleep now.")

# Entrar en deep sleep
deepsleep()