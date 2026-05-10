# ESP-NOW Comunicación entre 4 ESP32 (Topología Cuadrado) con Display, 2 Push Buttons y ACK
# Cada nodo envía mensajes de entre 3 opciones predefinidas y reenvía (solo una vez) para que todos los nodos puedan leerlos.
# Se evita retroalimentación infinita usando el prefijo "FWD:" en mensajes reenviados.
# RILB

from machine import Pin
from oled import Display
import network
import espnow
import time

# ---------------- CONFIGURACIÓN DEL NODO ----------------
# Define el ID de este nodo (cámbialo a "ESP1", "ESP2", "ESP3" o "ESP4" según corresponda)
DEVICE_ID = "ESP2"  # Ejemplo: este nodo es ESP2

# Define las MAC reales de cada nodo (reemplaza estos valores por los tuyos)
peers = {
    "ESP1": b'\x9c\x9c\x1f\xc8\x07\xc4',      	# ---- de Joel
    #"ESP2": b'\x08\xb6\x1f7\xd6\\',  			# ---- de LAB
    "ESP3": b'\xa0\xb7eb\xef\xa4',              # ---- de Lucero
    "ESP4": b'\x94<\xc62\xca0'                  # ---- de RILB
}

# Configuración de conectividad según topología (cuadrado):
# Ejemplo:
#    ESP1 ----- ESP2
#     |          |
#    ESP4 ----- ESP3
neighbors_ids = {
    "ESP1": ["ESP2", "ESP3", "ESP4"],
    "ESP2": ["ESP1", "ESP3", "ESP4"],  # ESP2 tiene como vecinos a ESP1, ESP3 y ESP4
    "ESP3": ["ESP2", "ESP4", "ESP1"],
    "ESP4": ["ESP1", "ESP3", "ESP2"]
}
# Lista de vecinos (MACs) para este nodo:
neighbors = [peers[n] for n in neighbors_ids[DEVICE_ID]]

# Mapeo de alias: se usa para mostrar nombres cortos en el display.
alias = {peers[k]: k for k in peers if k != DEVICE_ID}  # No se incluye el propio nodo

# ---------------- INICIALIZACIÓN WIFI y ESP-NOW ----------------
w0 = network.WLAN(network.STA_IF)
w0.active(True)

# Inicializa ESP-NOW
esp = espnow.ESPNow()
esp.active(True)  # Activa ESP-NOW

# Agrega a ESP-NOW solo los peers vecinos (no se agrega el propio)
for mac in neighbors:
    esp.add_peer(mac)

# ---------------- MENSAJES PREDEFINIDOS ----------------
mensajes = ["Holi", "me reporto", "AYUDAAA!"]
indice_msg = 0

# ---------------- DISPLAY OLED ----------------
display = Display()
def mostrar_menu():
    display.text("Mensaje actual:\n{}".format(mensajes[indice_msg]))

display.text("{} listo".format(DEVICE_ID))
time.sleep(2)
mostrar_menu()

# ---------------- CONFIGURACIÓN DE LOS BOTONES ----------------
# Se usan dos push buttons: uno para cambiar el mensaje y otro para enviarlo.
# Ajusta los pines según tus conexiones.
boton_cambiar = Pin(16, Pin.IN, Pin.PULL_UP)  # Botón para cambiar mensaje
boton_enviar = Pin(4, Pin.IN, Pin.PULL_UP)    # Botón para enviar mensaje

ultimo_estado_cambiar = boton_cambiar.value()
ultimo_estado_enviar = boton_enviar.value()

# ---------------- ENVÍO CON ACK ----------------
def enviar_mensaje():
    global indice_msg
    # Componer mensaje: "DEVICE_ID:mensaje"
    msg_text = "{}:{}".format(DEVICE_ID, mensajes[indice_msg])
    display.text("Enviando:\n{}".format(mensajes[indice_msg]))
    
    # Reinicia el diccionario de ACK para cada vecino
    ack_status = {mac: False for mac in neighbors}
    
    # Envía el mensaje a todos los vecinos
    for mac in neighbors:
        esp.send(mac, msg_text.encode())
    
    # Espera 1 segundo para recolectar ACKs
    start_ack = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start_ack) < 1000:
        if esp.any():
            host, rec = esp.recv()
            rec_text = rec.decode()
            if rec_text.startswith("Recibio??:\n"):
                ack_status[host] = True
        time.sleep(0.05)
    
    # Construye el resumen de recepción
    resumen = "Recibio??:\n"
    for mac in neighbors:
        nombre = alias.get(mac, mac.hex()[:8])
        if ack_status.get(mac, False):
            resumen += " {} Si".format(nombre)
        else:
            resumen += " {} NO".format(nombre)
    display.text(resumen)
    time.sleep(2)
    mostrar_menu()

# ---------------- BUCLE PRINCIPAL ----------------
while True:
    estado_cambiar = boton_cambiar.value()
    estado_enviar = boton_enviar.value()

    if estado_cambiar == 0 and ultimo_estado_cambiar == 1:
        # Cambia el mensaje (incrementa el índice)
        indice_msg = (indice_msg + 1) % len(mensajes)
        mostrar_menu()
        time.sleep(0.3)

    if estado_enviar == 0 and ultimo_estado_enviar == 1:
        enviar_mensaje()
    
    ultimo_estado_cambiar = estado_cambiar
    ultimo_estado_enviar = estado_enviar

    # Procesar recepción de mensajes
    if esp.any():
        host, rec = esp.recv()
        rec_text = rec.decode()
        # Si el mensaje recibido es un ACK, se ignora en esta sección.
        if not rec_text.startswith("Recibio??:\n"):
            # Se asume que el mensaje es "SENDER:contenido"
            parts = rec_text.split(":", 1)
            if len(parts) == 2:
                sender, contenido = parts
            else:
                sender, contenido = "?", rec_text

            # Si el mensaje proviene de este mismo nodo, lo ignora para evitar bucles.
            if sender == DEVICE_ID:
                # No se procesa el mensaje para evitar que ESP2 procese su propio mensaje reenviado.
                pass
            else:
                # Si el mensaje no tiene el prefijo "FWD:" (es original), lo reenviamos a los vecinos (excepto al que lo envió)
                if not rec_text.startswith("FWD:"):
                    forward_text = "FWD:" + rec_text
                    for mac in neighbors:
                        if mac != host:
                            esp.send(mac, forward_text.encode())
                # Muestra el mensaje recibido en el display
                sender_alias = alias.get(host, host.hex()[:8])
                display.text("[{}]:\n{}".format(sender_alias, contenido))
                time.sleep(2)
                mostrar_menu()
                # Envía ACK de vuelta al remitente
                ack_msg = "Recibio??:\n" + DEVICE_ID
                esp.send(host, ack_msg.encode())
