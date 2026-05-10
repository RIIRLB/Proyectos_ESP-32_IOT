## Deep Sleep + contador
import esp32
from machine import Pin, deepsleep, RTC
from time import sleep

# Configuración del RTC
rtc = RTC()

# Configuración de botones
stop_button = Pin(4, Pin.IN, Pin.PULL_UP)  # Botón para detener y entrar en deep sleep
start_button = Pin(12, Pin.IN, Pin.PULL_UP)  # Botón para despertar y continuar

# Configuración del pin para despertar desde deep sleep
esp32.wake_on_ext0(pin=start_button, level=esp32.WAKEUP_ALL_LOW)

# Verificar si hay un contador guardado en el RTC
if rtc.memory():
    try:
        contador = int(rtc.memory())  # Cargar el último número guardado
        print("-------------------------------------------------------------------------")
        print("ESP-32 preparada y lista para contar")
        print(f"Nos quedamos en: {contador}")  # Mensaje al despertar
    except ValueError:
        contador = 1  # Si hay un error en el RTC, reinicia el contador
else:
    contador = 1  # Si no hay nada guardado, inicia desde 1

# Comenzar o continuar el conteo
while contador <= 100:
    print("Contador:", contador)
    contador += 1  # Incrementa el contador

    # Verifica si el botón de detener se presiona
    if stop_button.value() == 0:  # Botón presionado (nivel bajo)
        print(f"Guardando progreso en {contador} y entrando en deep sleep...")
        print("-------------------------------------------------------------------------")
        rtc.memory(str(contador))  # Guarda el progreso en el RTC
        sleep(0.1)  # Prevención de rebotes
        deepsleep()  # Entra en deep sleep hasta que se despierte con el otro botón

    sleep(1)  # Pausa entre números para facilitar la visualización

print("¡El contador ha terminado!")
