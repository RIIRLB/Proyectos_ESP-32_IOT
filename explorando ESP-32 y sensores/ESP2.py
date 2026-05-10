# ESP-NOW-ver2 Comunicacion de forma más sencilla ESP2
#RILB
import network
import espnow
from machine import Pin
from time import sleep

# Configuración de ESP-NOW
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
esp = espnow.ESPNow()
esp.active(True)

# MAC de ESP con el que se comunica
peers = [b'\x94<\xc62\xca0']  # MAC de ESP1
for peer in peers:
    esp.add_peer(peer)

# Configuración de botones y LEDs
button_send = Pin(5, Pin.IN, Pin.PULL_UP)
led_esp1 = Pin(2, Pin.OUT)

# Variables para control de estado y debounce
last_state = 1  # Estado anterior del botón

def send_state(state):
    msg = "ON".encode() if state == 0 else "OFF".encode()
    for peer in peers:
        esp.send(peer, msg)
    print(f"Enviado: {msg.decode()}")

while True:
    current_state = button_send.value()
    
    # Solo enviar si el estado cambió
    if current_state != last_state:
        send_state(current_state)
        last_state = current_state
        sleep(0.1)  # Pequeño delay para debounce
    
    # Recibir mensajes de otros ESPs
    host, msg = esp.recv()
    if msg:
        decoded_msg = msg.decode()
        print(f"[{host}] {decoded_msg}")
        led_esp1.value(1 if decoded_msg == "ON" else 0)
