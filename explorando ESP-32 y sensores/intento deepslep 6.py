import esp32
from machine import Pin, deepsleep, RTC
from time import sleep

wake1 = Pin(14, Pin.IN, Pin.PULL_DOWN)
rtc = RTC()

# Verifica si hay datos en la memoria RTC
i = rtc.memory()
if i:
    i = int(i)
else:
    i = 10

# Configura el pin para despertar
esp32.wake_on_ext0(pin=wake1, level=esp32.WAKEUP_ANY_HIGH)

# Tu código principal va aquí para realizar una tarea
while i > 0:
    print('Going to sleep now in', i)
    i -= 1
    rtc.memory(str(i))  # Guarda el estado en la memoria RTC
    sleep(1)  # Espera un segundo antes de continuar

# Entra en deep sleep después de completar la cuenta regresiva
print('Entrando en deep sleep.......................')
deepsleep()

# Limpia la memoria RTC después de completar la cuenta regresiva
rtc.memory(b'')
print('ESP32 ha despertado')