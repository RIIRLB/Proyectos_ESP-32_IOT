# ESP-NOW Comunicación entre 4 ESP32 (Topología Cuadrado) con Display, Joystick y ACK
# RILB

from machine import Pin, ADC
from oled import Display
import network
import espnow
import time

# ---------------- CONFIGURACIÓN DEL NODO ----------------
# Define el ID de este nodo (cámbialo a "ESP1", "ESP2", "ESP3" o "ESP4" según corresponda)
DEVICE_ID = "ESP3"  # Ejemplo: este nodo es ESP3

# Define las MAC reales de cada nodo (reemplaza estos valores por los tuyos)
peers = {
    "ESP1": b'\x9c\x9c\x1f\xc8\x07\xc4',		#----de Joel
    "ESP2": b'\x08\xb6\x1f7\xd6\\',  		#----de LAB
    #"ESP3": b'\xa0\xb7eb\xef\xa4',			#----de Lucero
    "ESP4": b'\x94<\xc62\xca0'				#----de RILB

}

# Configuración de conectividad según topología (cuadrado):
# Ejemplo:
#    ESP1 ----- ESP2
#     |          |
#    ESP4 ----- ESP3
neighbors_ids = {
    "ESP1": ["ESP2", "ESP4"],
    "ESP2": ["ESP1", "ESP3"],
    "ESP3": ["ESP2", "ESP4"],
    "ESP4": ["ESP1", "ESP3"]
}
# Lista de vecinos (MACs) para este nodo:
neighbors = [peers[n] for n in neighbors_ids[DEVICE_ID]]

# Mapeo de alias: se usa para mostrar nombres cortos en el display.
alias = {peers[k]: k for k in peers if k != DEVICE_ID}  # no se incluye el propio nodo

# ---------------- INICIALIZACIÓN WIFI y ESP-NOW ----------------
w0 = network.WLAN(network.STA_IF)
w0.active(True)

#Inicializa ESP-NOW
esp = espnow.ESPNow()
esp.active(True)  # Activa ESP-NOW

# Agrega a ESP-NOW solo los peers vecinos (no agregamos el propio)
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

# ---------------- CONFIGURACIÓN DEL JOYSTICK ----------------
# Se usará el eje X (ADC) y el botón del joystick.
joystick_x = ADC(Pin(32))      # Eje X en GPIO32-ADC1
joystick_btn = Pin(2, Pin.IN, Pin.PULL_UP)  # Botón en GPIO2

# Umbrales para detectar movimiento (ajusta según tu joystick)
umbral_izq = 3000
umbral_der = 4500

last_move = time.ticks_ms()
last_btn = joystick_btn.value()

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
    
    # Espera 2 segundos para recolectar ACKs
    start_ack = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start_ack) < 1000:
        if esp.any():
            host, rec = esp.recv()
            rec_text = rec.decode()
            if rec_text.startswith("ACK:"):
                # Registro de ACK: se asume que el ACK es "ACK:remitente"
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
    # Lectura del eje X para cambiar mensaje
    x_val = joystick_x.read()
    ahora = time.ticks_ms()
    if time.ticks_diff(ahora, last_move) > 300:
        if x_val < umbral_izq:
            indice_msg = (indice_msg - 1) % len(mensajes)
            mostrar_menu()
            last_move = ahora
        elif x_val > umbral_der:
            indice_msg = (indice_msg + 1) % len(mensajes)
            mostrar_menu()
            last_move = ahora

    # Botón del joystick para enviar mensaje
    btn_val = joystick_btn.value()
    if btn_val == 0 and last_btn == 1:
        enviar_mensaje()
    last_btn = btn_val

    # Procesar recepción de mensajes
    if esp.any():
        host, rec = esp.recv()
        rec_text = rec.decode()
        # Si se recibe un ACK, se procesa en la función de envío; si es un mensaje normal:
        if not rec_text.startswith("ACK:"):
            # Se asume que el mensaje es "SENDER:contenido"
            parts = rec_text.split(":", 1)
            if len(parts) == 2:
                sender, contenido = parts
            else:
                sender, contenido = "?", rec_text
            # Muestra el mensaje recibido
            sender_alias = alias.get(host, host.hex()[:8])
            display.text("[{}]:\n{}".format(sender_alias, contenido))
            time.sleep(2)
            mostrar_menu()
            # Envía ACK de vuelta al remitente
            ack_msg = "ACK:" + DEVICE_ID
            esp.send(host, ack_msg.encode())
    time.sleep(0.1)
