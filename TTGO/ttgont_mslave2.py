# ESP-RILB Slave con reenvío de datos hacia TTGO1

from machine import Pin
import network
import espnow
import time
from oled import Display  # Es OLED externo

# ========== CONFIGURACIÓN ==========
DEVICE_ID = "ESP2"  # Cambiar según nodo
MACS = {
    "TTGO1": b'\x04\x98z\xa6o\xa0',   			# Maestro
    "ESP2": b'\x94<\xc62\xca0'                 	# ---- de RILB
    "TTGO2": b'\xa0\xb7eb\xef\xa4',
    "ESP4": b'\x08\xb6\x1f7\xd6\\',  			# ---- de LAB
}

# Vecinos que esta ESP ve físicamente
VECINOS = ["TTGO1", "TTGO2"]  # Cambiar según cada nodo

ultimo_mensaje = f"{DEVICE_ID} listo"

# ========== WIFI + ESP-NOW ==========
w0 = network.WLAN(network.STA_IF)
w0.active(True)

esp = espnow.ESPNow()
esp.active(True)

# Agregar solo vecinos visibles
for nombre in VECINOS:
    try:
        esp.add_peer(MACS[nombre])
    except OSError:
        pass

# ========== DISPLAY ==========
display = Display()
display.text(f"{DEVICE_ID} OK")

def enviar_datos(mac):
    global ultimo_mensaje
    mensaje = f"DATA:{DEVICE_ID}:{ultimo_mensaje}"
    esp.send(mac, mensaje.encode())
    display.text(f"Enviado:\n{ultimo_mensaje}")

def reenviar_a_ttgo(data):
    esp.send(MACS["TTGO1"], data)
    display.text("Reenviado a TTGO1")

# ========== LOOP ==========
while True:
    if esp.any():
        host, msg = esp.recv()
        texto = msg.decode()

        # Solicitud directa de TTGO1
        if texto == "REQ_DATA" and host == MACS["TTGO1"]:
            enviar_datos(host)

        # Solicitud que llega de otro nodo → reenviar
        elif texto == "REQ_DATA" and host != MACS["TTGO1"]:
            reenviar_a_ttgo(msg)

        # Datos que llegan de otro nodo → reenviar
        elif texto.startswith("DATA:") and host != MACS["TTGO1"]:
            reenviar_a_ttgo(msg)

        # ACK recibido
        elif texto.startswith("ACK:"):
            display.text(f"ACK de {texto[4:]}")

    time.sleep(0.05)