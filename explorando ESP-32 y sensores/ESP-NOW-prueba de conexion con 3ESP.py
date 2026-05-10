#1ra prueba de conexion con 3ESP, necesita 3 displays OLED, y 3 Joysticks
#RILB
import network
import espnow
from machine import Pin, I2C
from ssd1306 import SSD1306_I2C

# Configuración del OLED
WIDTH, HEIGHT = 128, 64  # Tamaño de la pantalla OLED
i2c = I2C(0, scl=Pin(22), sda=Pin(21))
display = SSD1306_I2C(WIDTH, HEIGHT, i2c)

def show_message(msg):
    display.fill(0)
    display.text(msg, 0, 20)
    display.show()

# Configuración de ESP-NOW
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
esp = espnow.ESPNow()
esp.active(True)

# Lista de pares (MACs de ESP-32)
peers = [b'\xXX\xXX\xXX\xXX\xXX\xXX',  # ESP-1
         b'\xXX\xXX\xXX\xXX\xXX\xXX',  # ESP-2
         b'\xXX\xXX\xXX\xXX\xXX\xXX']  # ESP-3

for peer in peers:
    esp.add_peer(peer)

# Configuración del joystick
joystick = Pin(34, Pin.IN)  # Ejemplo, cambiar según conexión
messages = ["Hola", "Cómo estás?", "Adiós"]
selected = 0

# Función para cambiar mensaje
def change_message():
    global selected
    selected = (selected + 1) % len(messages)
    show_message(f"Enviar: {messages[selected]}")

# Configurar interrupción para el joystick
joystick.irq(trigger=Pin.IRQ_FALLING, handler=lambda p: change_message())

# Bucle principal
while True:
    host, msg = esp.recv()
    if msg:
        formatted_msg = f"[{host}] {msg.decode()}"
        show_message(formatted_msg)
        
        # Retransmitir si es ESP-2
        if b'\xXX\xXX\xXX\xXX\xXX\xXX' == peers[1]:  # MAC de ESP-2
            for peer in peers:
                if peer != host:
                    esp.send(peer, msg)
