# TTGO1 Maestro: Recolector de mensajes por ESP-NOW con ACK y pantalla

from machine import Pin
import network
import espnow
import time
import tft_config
import st7789py as st7789
from print import Print

# ——— Configuración del Display ———
tft = tft_config.config(rotation=1)
tft.fill(st7789.BLACK)
pantalla = Print(tft, fg=st7789.CYAN)

# ——— Configuración de MACs y Vecinos ———
DEVICE_ID = "TTGO1"
peers = {
    "ESP2": b'\x08\xb6\x1f7\xd6\x5c',		#RILB
    "ESP3": b'\xa0\xb7eb\xef\xa4',			#TTGO2
    "ESP4": b'\x94<\xc62\xca0'				#ESPLAB
}
alias = {v: k for k, v in peers.items()}

# ——— Inicializa WiFi y ESP-NOW ———
w0 = network.WLAN(network.STA_IF)
w0.active(True)
esp = espnow.ESPNow()
esp.active(True)

# Agrega a los vecinos como peers
for mac in peers.values():
    try:
        esp.add_peer(mac)
    except OSError as e:
        print("Peer ya existe:", e)

# Mensaje de bienvenida
pantalla(None, "TTGO1 listo", 10, 10, st7789.CYAN, st7789.BLACK)
time.sleep(2)

# ——— Botón para iniciar recolección ———
boton = Pin(0, Pin.IN, Pin.PULL_UP)  # Cambiar si prefieres otro pin
ultimo_estado = boton.value()

# ——— Función para recolectar mensajes ———
def recolectar():
    tft.fill(st7789.BLACK)
    pantalla(None, "Solicitando datos...", 10, 10, st7789.WHITE, st7789.BLACK)

    # Envía solicitud a todos
    for mac in peers.values():
        esp.send(mac, b"REQ_DATA")

    datos = {}
    ack_status = {}
    inicio = time.ticks_ms()

    # Espera respuestas durante 3 segundos
    while time.ticks_diff(time.ticks_ms(), inicio) < 3000:
        if esp.any():
            host, msg = esp.recv()
            if msg.startswith(b"DATA:"):
                texto = msg[5:].decode()
                datos[host] = texto
                # Responder con ACK
                esp.send(host, b"ACK:" + DEVICE_ID.encode())
                ack_status[host] = True
            elif msg.startswith(b"ACK:"):
                ack_status[host] = True

    # Muestra resultados
    y = 30
    for mac in peers.values():
        nombre = alias.get(mac, mac.hex()[:6])
        mensaje = datos.get(mac, "Sin respuesta")
        pantalla(None, f"{nombre}: {mensaje}", 10, y, st7789.YELLOW, st7789.BLACK)
        y += 30

# ——— Bucle principal ———
while True:
    estado = boton.value()
    if estado == 0 and ultimo_estado == 1:
        recolectar()
        time.sleep(0.3)  # Anti-rebote
    ultimo_estado = estado