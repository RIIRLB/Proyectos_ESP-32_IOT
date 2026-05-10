# Emisor
import espnow
import network
from machine import Pin
from time import sleep

# Configurar WiFi en modo Station
wlan = network.WLAN(network.STA_IF)
wlan.active(True)

# Crear la instancia de ESP-NOW
esp = espnow.ESPNow()
esp.active(True)
peer_mac = b'\x08\xb6\x1f7\xd6\\'  # Cambia por la MAC del receptor
esp.add_peer(peer_mac)

# Configurar el botón
button = Pin(4, Pin.IN, Pin.PULL_UP)

estado_anterior = 1  # Inicialmente el botón no está presionado

# Enviar mensajes al presionar o soltar el botón
while True:
    estado_actual = button.value()  # Leer estado del botón
    if estado_actual != estado_anterior:  # Solo enviar cuando haya un cambio
        if estado_actual == 0:
            print("¡Botón presionado! Enviando señal...")
            esp.send(peer_mac, b"MORSE")  # Enviar "MORSE"
        else:
            print("Botón liberado, apagando señal...")
            esp.send(peer_mac, b"0")  # Enviar mensaje vacío para apagar el LED

    estado_anterior = estado_actual  # Actualizar estado anterior
    sleep(0.01)  # Pequeña pausa para evitar rebotes
