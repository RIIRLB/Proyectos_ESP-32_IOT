# Receptor
import espnow
import network
from machine import Pin

# Configurar WiFi en modo Station
wlan = network.WLAN(network.STA_IF)
wlan.active(True)

# Crear la instancia de ESP-NOW
esp = espnow.ESPNow()
esp.active(True)

# Configurar LED en el pin 2 (ajusta según tu hardware)
led = Pin(2, Pin.OUT)
led.value(0)  # Apagar LED inicialmente

print("Esperando mensajes...")

# Escuchar mensajes entrantes
while True:
    host, msg = esp.recv()  # Recibe datos (host = MAC del emisor, msg = mensaje)
    if msg:
        mensaje = msg.decode()
        print(f"Mensaje recibido de {host}: {mensaje}")
        
        if mensaje == "MORSE":
            led.value(1)  # Encender LED (botón presionado)
        else:
            led.value(0)  # Apagar LED (botón liberado)