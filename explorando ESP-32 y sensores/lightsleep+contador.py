#LightSleep+Contador
from machine import Pin, lightsleep
from time import sleep

# Botones
sleep_button = Pin(4, Pin.IN, Pin.PULL_UP)  # Botón para entrar en modo "sleep"
wake_button = Pin(5, Pin.IN, Pin.PULL_UP)  # Botón para "despertar"

# Variable para el progreso del contador
contador = 1  # Empezamos desde 1

# Loop principal
while contador <= 100:
    print("Contador:", contador)
    contador += 1  # Incrementa el contador
    sleep(1)  # Pausa entre números para facilitar la visualización

    # Verifica si el botón de "sleep" se presiona
    if sleep_button.value() == 0:  # Botón presionado (nivel bajo)
        print("Entrando en modo light sleep...")
        
        # Espera a que se presione el botón de "despertar"
        while wake_button.value() != 0:  # Mientras el botón no esté presionado
            lightsleep(100)  # Light sleep por 100 ms (menor consumo)

        print("Despertando y continuando...")
        sleep(0.1)  # Prevención de rebotes

print("¡El contador ha terminado!")
