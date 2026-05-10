# ESP-NOW Comunicación entre 4 ESP32 (Topología Cuadrado) con Display, 2 Push Buttons y ACK
# TTGO
# RILB
from machine import Pin
import network
import espnow
import time
import tft_config
import st7789py as st7789
import comfortaa_24 as font

# --- CONFIGURACIÓN GENERAL ---
DEVICE_ID = "TTGO1"
MAC_SELF = b'4\x98z\xa6o\xa0'  # MAC propia

# MACs de los otros dispositivos
peers = {
    "ESP1": b'\x94<\xc62\xca0',
    "ESP2": b'\x08\xb6\x1f7\xd6\\',
    "ESP3": b'\xa0\xb7eb\xef\xa4',
}

neighbors = list(peers.values())
alias = {peers[k]: k for k in peers if k != DEVICE_ID}

mensajes = ["Holi", "me reporto", "AYUDAAA!"]
indice_msg = 0

# --- DISPLAY ---
tft = tft_config.config(rotation=1)
#tft.init()
tft.fill(st7789.BLACK)

def mostrar(texto, y=10, color=st7789.WHITE):
    tft.fill_rect(0, y, tft.width, font.HEIGHT, st7789.BLACK)
    tft.write(font, texto, 10, y, color, st7789.BLACK)

def mostrar_menu():
    mostrar("Mensaje:", 5, st7789.CYAN)
    mostrar(mensajes[indice_msg], 45, st7789.YELLOW)

mostrar("TTGO listo", 5, st7789.GREEN)
time.sleep(1)
mostrar_menu()

# --- BOTONES ---
boton_cambiar = Pin(0, Pin.IN)   # botón izquierdo TTGO
boton_enviar = Pin(35, Pin.IN)   # botón derecho TTGO
estado_anterior_cambiar = boton_cambiar.value()
estado_anterior_enviar = boton_enviar.value()

# --- INICIA ESP-NOW ---
w0 = network.WLAN(network.STA_IF)
w0.active(False)
time.sleep(0.5)
w0.active(True)

esp = espnow.ESPNow()
esp.active(True)
for mac in neighbors:
    try:
        esp.add_peer(mac)
    except OSError as e:
        if 'ESP ERR ESPNOW EXIST' not in str(e):
            raise  # solo ignora si es el error de que ya existe

# --- FUNCIÓN PARA ENVIAR MENSAJE ---
def enviar_mensaje():
    global indice_msg
    msg_text = "{}:{}".format(DEVICE_ID, mensajes[indice_msg])
    mostrar("Enviando...", 10, st7789.CYAN)
    mostrar(mensajes[indice_msg], 50, st7789.YELLOW)

    ack_status = {mac: False for mac in neighbors}
    for mac in neighbors:
        esp.send(mac, msg_text.encode())

    inicio = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), inicio) < 1000:
        if esp.any():
            host, rec = esp.recv()
            if rec.decode().startswith("Recibio??:\n"):
                ack_status[host] = True
        time.sleep(0.05)

    # Mostrar ACKs
    y = 80
    mostrar("Recibieron:", y, st7789.CYAN)
    for mac in neighbors:
        nombre = alias.get(mac, mac.hex()[:6])
        estado = "SI" if ack_status[mac] else "NO"
        mostrar(f"{nombre}: {estado}", y + 30, st7789.GREEN if ack_status[mac] else st7789.RED)
        y += 30
    time.sleep(2)
    mostrar_menu()

# --- LOOP PRINCIPAL ---
while True:
    if boton_cambiar.value() == 0 and estado_anterior_cambiar == 1:
        indice_msg = (indice_msg + 1) % len(mensajes)
        mostrar_menu()
        time.sleep(0.3)

    if boton_enviar.value() == 0 and estado_anterior_enviar == 1:
        enviar_mensaje()

    estado_anterior_cambiar = boton_cambiar.value()
    estado_anterior_enviar = boton_enviar.value()

    if esp.any():
        host, rec = esp.recv()
        texto = rec.decode()
        if texto.startswith("Recibio??:\n"):
            continue  # no mostramos ACK

        if not texto.startswith("FWD:"):
            for mac in neighbors:
                if mac != host:
                    esp.send(mac, ("FWD:" + texto).encode())

        sender = alias.get(host, host.hex()[:6])
        parts = texto.split(":", 1)
        contenido = parts[1] if len(parts) == 2 else texto
        mostrar(f"[{sender}]", 10, st7789.CYAN)
        mostrar(contenido, 50, st7789.YELLOW)
        time.sleep(2)
        mostrar_menu()
        ack = f"Recibio??:\n{DEVICE_ID}"
        esp.send(host, ack.encode())
