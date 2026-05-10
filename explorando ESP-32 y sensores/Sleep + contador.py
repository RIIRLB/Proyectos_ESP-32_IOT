#Sleep + contador 
from machine import Pin
from time import sleep

# Botones
sleep_button = Pin(4, Pin.IN, Pin.PULL_UP)  # Botón para entrar en modo "sleep"
wake_button = Pin(5, Pin.IN, Pin.PULL_UP)  # Botón para continuar el conteo

# Variable para el progreso del contador
contador = 1  # Empezamos desde 1

# Loop principal
while contador <= 100:
    print("Contador:", contador)
    contador += 1  # Incrementa el contador

    # Verifica si el botón de "sleep" se presiona
    if sleep_button.value() == 0:  # Botón presionado (nivel bajo)
        print("Entrando en modo de espera...")
        while True:
            # Espera a que se presione el botón para "despertar"
            if wake_button.value() == 0:  # Botón presionado (nivel bajo)
                print("Despertando y continuando...")
                sleep(0.2)  # Prevención de rebotes
                break

    sleep(1)  # Pausa entre números para facilitar la visualización

print("¡El contador ha terminado!")

