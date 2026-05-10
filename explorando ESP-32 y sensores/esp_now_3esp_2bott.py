# ESP-NOW Comunicación entre 3 ESP32 con Display OLED y 2 botones
# RILB
from machine import Pin
import time
import network
import espnow
from oled import OLED

# Inicializar WiFi y ESP-NOW
w0 = network.WLAN(network.STA_IF)
w0.active(True)

esp = espnow.ESPNow()
esp.init()

# --- CONFIGURACIONES PERSONALIZABLES ---
DEVICE_ID = "ESP1"  # Cambiar a ESP2 o ESP3 según el dispositivo
PEERS = {
    "ESP1": b"\xFF\xFF\xFF\xFF\xFF\xFF",  # MAC de ESP1
    "ESP2": b"\xFF\xFF\xFF\xFF\xFF\xFF",  # MAC de ESP2
    "ESP3": b"\xFF\xFF\xFF\xFF\xFF\xFF",  # MAC de ESP3
}

# Agregar los peers necesarios para cada ESP
if DEVICE_ID == "ESP1":
    esp.add_peer(PEERS["ESP2"])
elif DEVICE_ID == "ESP2":
    esp.add_peer(PEERS["ESP1"])
    esp.add_peer(PEERS["ESP3"])
elif DEVICE_ID == "ESP3":
    esp.add_peer(PEERS["ESP2"])

# Mensajes disponibles
messages = ["Holi", "me reporto", "AYUDAAA!"]
selected_index = 0

oled = OLED()
oled.text(f"{DEVICE_ID} listo!")

# Botones
btn_next = Pin(13, Pin.IN, Pin.PULL_UP)   # Cambiar según conexiones
btn_send = Pin(12, Pin.IN, Pin.PULL_UP)

# Control de rebote
def wait_release(pin):
    while pin.value() == 0:
        time.sleep_ms(50)

# Función para enviar mensaje
def send_message():
    msg = f"{DEVICE_ID}:{messages[selected_index]}"
    if DEVICE_ID == "ESP2":
        esp.send(PEERS["ESP1"], msg)
        esp.send(PEERS["ESP3"], msg)
    elif DEVICE_ID == "ESP1":
        esp.send(PEERS["ESP2"], msg)
    elif DEVICE_ID == "ESP3":
        esp.send(PEERS["ESP2"], msg)
    oled.text(f"Enviado: {messages[selected_index]}")

# Bucle principal
while True:
    if btn_next.value() == 0:
        selected_index = (selected_index + 1) % len(messages)
        oled.text(f"Seleccionado: {messages[selected_index]}")
        wait_release(btn_next)

    if btn_send.value() == 0:
        send_message()
        wait_release(btn_send)

    if esp.any():
        mac, msg = esp.recv()
        try:
            sender, text = msg.decode().split(":")
            oled.text(f"[{sender}] {text}")

            # Si es ESP2, reenviar si no fue ella quien lo originó
            if DEVICE_ID == "ESP2" and sender != "ESP2":
                forward_to = "ESP1" if sender == "ESP3" else "ESP3"
                esp.send(PEERS[forward_to], msg)

        except Exception as e:
            oled.text(f"Error al recibir")

    time.sleep_ms(100)
