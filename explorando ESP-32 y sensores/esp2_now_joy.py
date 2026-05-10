# ESP-NOW Comunicación entre 3 ESP32 con Display Display y Joystic
# RILB
import network
import espnow
import time
from machine import Pin, ADC
from oled import Display
                                                # ESP2
# ----- Inicialización Wi-Fi y ESP-NOW -----
w0 = network.WLAN(network.STA_IF)
w0.active(True)

#Inicializa ESP-NOW
esp = espnow.ESPNow()
esp.active(True)  # Activa ESP-NOW

# Agrega los peers (Reemplaza las MAC reales)
peer1 = b'\x94<\xc62\xca0'     		# MAC de ESP1
peer3 = b'\x08\xb6\x1f7\xd6\\'   	2# MAC de ESP3

esp.add_peer(peer1)
esp.add_peer(peer3)

# ----- Mensajes predefinidos -----
mensajes = ["Holi", "me reporto", "AYUDAAA!"]
indice_msg = 0

# ----- Inicializa Display -----
display = Display()
display.text("ESP2 lista para enviar")

def mostrar_menu():
    display.text("Mensaje actual:\n{}".format(mensajes[indice_msg]))

mostrar_menu()

# ----- Configuración del joystick -----
# Se asume que el eje X del joystick está conectado al pin ADC 34
adc_x = ADC(Pin(34))
adc_x.atten(ADC.ATTN_11DB)  # Permite medir de 0 a 3.3V (rango completo)

# Define umbrales (ajusta según tu módulo)
umbral_alto = 2500   # Si el valor ADC es mayor, se considera que se movió a la derecha
umbral_bajo  = 1500   # Si es menor, se considera que se movió a la izquierda

# Se utiliza el botón del joystick para enviar (conectado a GPIO15)
joystick_button = Pin(15, Pin.IN, Pin.PULL_UP)

# Variables para debouncing
last_move_time = time.ticks_ms()
last_button_state = joystick_button.value()

# ----- Bucle principal -----
while True:
    # Lee el valor del eje X del joystick
    x_val = adc_x.read()
    now = time.ticks_ms()
    
    # Si se mueve (derecha o izquierda) y pasan al menos 300 ms para debouncing:
    if time.ticks_diff(now, last_move_time) > 300:
        if x_val > umbral_alto:
            indice_msg = (indice_msg + 1) % len(mensajes)
            mostrar_menu()
            last_move_time = now
        elif x_val < umbral_bajo:
            indice_msg = (indice_msg - 1) % len(mensajes)
            mostrar_menu()
            last_move_time = now

    # Verifica el botón para enviar
    button_state = joystick_button.value()
    if button_state == 0 and last_button_state == 1:
        msg = mensajes[indice_msg]
        display.text("Enviando:\n" + msg)
        # Envía a ambos peers (ESP1 y ESP3)
        esp.send(peer1, msg.encode())
        esp.send(peer3, msg.encode())
        time.sleep(0.3)
        mostrar_menu()
    last_button_state = button_state

    # ----- Recepción y retransmisión de mensajes -----
    if esp.any():
        host, rec_msg = esp.recv()
        # Si ESP2 recibe un mensaje de uno de los peers, lo reenvía al otro:
        if host == peer1:
            esp.send(peer3, rec_msg)
        elif host == peer3:
            esp.send(peer1, rec_msg)
        # Muestra el mensaje recibido en el display
        display.text("De [{}]:\n{}".format(host.hex(), rec_msg.decode()))
        time.sleep(2)
        mostrar_menu()

    time.sleep(0.1)
