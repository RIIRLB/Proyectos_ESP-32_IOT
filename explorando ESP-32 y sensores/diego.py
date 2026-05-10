from machine import Pin, ADC, I2C, deepsleep, reset_cause, DEEPSLEEP_RESET, reset
import ssd1306
import time
import math
import esp32
import network
import espnow
import machine 
# Configuración OLED
i2c = I2C(0, scl=Pin(22), sda=Pin(21))
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

# Configuración botones
boton_opcion = Pin(32, Pin.IN, Pin.PULL_UP)
boton_ok = Pin(33, Pin.IN, Pin.PULL_UP)

# Configuración LED y buzzer
led = Pin(0, Pin.OUT)
buzzer = Pin(25, Pin.OUT)
led.value(0)
buzzer.value(0)

# Configuración sensor MQ-135
pin_sensor = 34
sensor = ADC(Pin(pin_sensor))
sensor.width(ADC.WIDTH_12BIT)
sensor.atten(ADC.ATTN_11DB)

# Constantes sensor
R0 = 1
VALOR_MAX_ADC = 4095
Volt_REFERENCIA = 3.3
R_CARGA = 10
lim_ALCOHOL = 100
lim_BENCENO = 50

# Función para escribir texto OLED con ajuste de línea
def oled_text_wordwrap(texto, x, y):
    max_caracteres = 16
    palabras = texto.split()
    linea_actual = ""
    linea_y = y
    for palabra in palabras:
        if len(linea_actual + palabra) + (1 if linea_actual else 0) <= max_caracteres:
            if linea_actual:
                linea_actual += " "
            linea_actual += palabra
        else:
            oled.text(linea_actual, x, linea_y)
            linea_y += 8
            if linea_y >= 64:
                break
            linea_actual = palabra
    if linea_y < 64 and linea_actual:
        oled.text(linea_actual, x, linea_y)

# Iniciar WiFi y ESP-NOW
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
espnow_interface = espnow.ESPNow()
espnow_interface.active(True)

# Variables globales
es_principal = True
macs_nodos = []

#modulo de barra de carga
def mostrar_barra_carga(tiempo_total=3):
    oled.fill(0)
    oled_text_wordwrap("Cargando programa...", 0, 0)
    ancho_total = 100  # Ancho máximo de la barra
    alto_barra = 10
    x_inicio = 14
    y_inicio = 30

    oled.rect(x_inicio, y_inicio, ancho_total, alto_barra, 1)  # Dibuja borde de la barra 

    pasos = 20  # Cuántos pasos tendrá la barra
    delay = tiempo_total / pasos

    for i in range(pasos + 1):
        ancho_actual = int((i / pasos) * (ancho_total - 2))
        porcentaje = int((i / pasos) *100)
        oled.fill_rect(x_inicio + 1, y_inicio + 1, ancho_actual, alto_barra - 2, 1)
        oled.fill_rect(0,45,128,10,0) #limpiar area del porcentaje
        oled.text("{}%".format(porcentaje), 50, 47)
        oled.show()
        time.sleep(delay)

#Declaramos el mensaje de bienvenida 
def mensaje_de_bienvenida():
    oled.fill(0)
    oled.text("Hola",45,25)
    oled_text_wordwrap("Querido usuario",5,35)
    oled.show()
    time.sleep(3)
    mostrar_barra_carga()
    oled.fill(0)
    oled.text("La carga",30,25)
    oled.text("ha sido",35,35)
    oled.text("completada",23,45)
    oled.show()
    time.sleep(3)
    oled.fill(0)
    oled.text("BIENVENIDO!",21,33)
    oled.show()
    time.sleep(3)
# Selección de rol al iniciar
def seleccionar_rol():
    global es_principal
    opciones_rol = ["Principal", "Receptor"]
    indice_rol = 0
    while True:
        oled.fill(0)
        oled_text_wordwrap("Seleccionar Rol:", 0, 0)
        for j,opcion in enumerate(opciones_rol):
            if indice_rol == j:
                oled.text(">",23,26 + 10*j)
            oled.text(opcion,33, 26 + 10*j)
        oled.show()

        if not boton_opcion.value():
            indice_rol = (indice_rol + 1) % len(opciones_rol)
            time.sleep(0.3)

        if not boton_ok.value():
            es_principal = (indice_rol == 0)
            time.sleep(0.3)
            break

# Calibración MQ-135
def calibrar_mq135():
    oled.fill(0)
    oled.text("Calibrando...", 15, 25)
    oled.show()
    time.sleep(5)
    valor_adc = sensor.read()
    voltaje = valor_adc / VALOR_MAX_ADC * Volt_REFERENCIA
    if voltaje == 0:
        oled.text("Error voltaje 0", 10, 20)
        oled.show()
        return None
    R0 = (Volt_REFERENCIA * R_CARGA / voltaje) - R_CARGA
    oled.fill(0)
    oled.text("Calibrado!", 20, 25)
    oled.show()
    time.sleep(2)
    return R0

# Buzzer
def activar_buzzer():
    buzzer.value(1)
    time.sleep(1)
    buzzer.value(0)

# Modo deep sleep controlado
def entrar_sleep():
    oled.fill(0)
    oled_text_wordwrap("Entrando a Deep Sleep", 0, 20)
    oled.show()
    time.sleep(2)
    oled.write_cmd(0xAE)
    led.value(0)
    esp32.wake_on_ext0(pin=boton_ok, level=0)
    deepsleep()


#modulo para cambiar de principal a receptor
def cambiar_rol():
    esperar_soltado(boton_ok)
    opciones = ["Cambiar de Rol", "Regresar"]
    i = 0
    while True:
        oled.fill(0)
        oled.text("MENU DE ROL", 20, 0)
        for j, opcion in enumerate(opciones):
            if i == j:
                esperar_soltado(boton_ok)
                oled.text(">", 0, 16 + 10*j)
            oled.text(opcion, 10, 16 + 10*j)
        oled.show()

        if not boton_opcion.value():
            i = (i + 1) % len(opciones)
            time.sleep(0.3)

        if not boton_ok.value():
            if i == 0:
                confirmar_cambio_rol()
            elif i == 1:
                break
            time.sleep(0.5)

def confirmar_cambio_rol():
    esperar_soltado(boton_ok)
    opciones_confirmacion = ["Si", "No"]
    i = 0
    while True:
        oled.fill(0)
        oled.text("CAMBIAR DE ROL", 7, 0)
        oled_text_wordwrap("Deseas reiniciar?", 0, 12)
        for j, opcion in enumerate(opciones_confirmacion):
            if i == j:
                oled.text(">", 0, 40 + 10*j)
            oled.text(opcion, 10, 40 + 10*j)
        oled.show()

        if not boton_opcion.value():
            i = (i + 1) % len(opciones_confirmacion)
            time.sleep(0.3)

        if not boton_ok.value():
            esperar_soltado(boton_ok)
            if i == 0:
                global es_principal
                es_principal = not es_principal
                oled.fill(0)
                oled_text_wordwrap("Cambiando de Rol...", 0, 20)
                oled.show()
                time.sleep(0.5)  # ← IMPORTANTE este delay
                machine.reset()
                seleccionar_rol()
            elif i == 1:
                break
            time.sleep(0.5)


# Nodo principal con submenús
def modo_principal():
    opciones = ["LED", "Mensaje", "Medicion", "Dormir Nodo", "Cambiar Rol"]
    indice_opcion = 0

    while True:
        oled.fill(0)
        oled_text_wordwrap("Menu Principal", 7, 0)
        for j,opcion in enumerate(opciones):
            if indice_opcion == j:
                oled.text(">",0, 16 + 10*j)
            oled.text(opcion, 10, 16 + 10*j)
        oled.show()

        if not boton_opcion.value():
            indice_opcion = (indice_opcion + 1) % len(opciones)
            time.sleep(0.3)

        if not boton_ok.value():
            if indice_opcion == 0:
                menu_led()
            elif indice_opcion == 1:
                menu_mensajes()
            elif indice_opcion == 2:
                menu_medicion()
            elif indice_opcion == 3:
                menu_dormir()
            elif indice_opcion == 4:
                cambiar_rol()
            time.sleep(0.5)

# Submenús
def esperar_soltado(boton):
    while not boton.value():
        pass
    time.sleep(0.05)

def menu_led():
    #esperar a que el boton se suelte antes de entrar al menu 
    esperar_soltado(boton_ok)
    
    opciones = ["Control de LED Principal", "Control de LED Receptor", "Regresar"]
    i = 0
    while True:
        oled.fill(0)
        oled_text_wordwrap("MENU LED", 28, 0)
        for j,opcion in enumerate(opciones):
            if i == j:
                oled.text(">", 0, 16+20*j)
            oled_text_wordwrap(opcion,10, 16+20*j)
        oled.show()
        
        if not boton_opcion.value():
            i = (i + 1) % len(opciones)
            time.sleep(0.5)
        if not boton_ok.value():
            esperar_soltado(boton_ok)
            if i == 0:
                menu_led_principal()
            elif i == 1:
                menu_led_receptor()
            elif i == 2:
                break
            time.sleep(0.5)
            
def menu_led_receptor():
    opciones = ["Encender LED", "Apagar LED", "Regresar"]
    i = 0
    while True:
        oled.fill(0)
        oled.text("MENU de LED",17,0)
        oled.text("Receptor", 30, 10)
        for j,opcion in enumerate(opciones):
            if i == j:
                oled.text(">", 0, 26+10*j)
            oled.text(opcion,10, 26+10*j)
        oled.show()
        
        if not boton_opcion.value():
            i = (i + 1) % len(opciones)
            time.sleep(0.3)
        if not boton_ok.value():
            esperar_soltado(boton_ok)
            if i == 0 or i == 1:
                nodo_destino = seleccionar_nodo()
                if nodo_destino is not None:
                    comando = "LED ON" if i == 0 else "LED OFF"
                    espnow_interface.send(nodo_destino,comando)
            elif i == 2:
                break
            time.sleep(0.5)

def menu_led_principal():
    opciones = ["Encender LED", "Apagar LED", "Regresar"]
    i = 0
    while True:
        oled.fill(0)
        oled.text("Menu de LED",17,0)
        oled.text("PROPIO",35,10)
        for j, opcion in enumerate(opciones):
            if i == j:
                oled.text(">", 0, 26+10*j)
            oled_text_wordwrap(opcion, 10, 26+10*j)
        oled.show()

        if not boton_opcion.value():
            i = (i + 1) % len(opciones)
            time.sleep(0.3)
        if not boton_ok.value():
            esperar_soltado(boton_ok)
            if i == 0:
                led.value(1)
            elif i == 1:
                led.value(0)
            elif i == 2:
                break
            time.sleep(0.5)

def menu_mensajes():
    esperar_soltado(boton_ok)
    mensajes = ["Para Principal", "Para Receptor", "Regresar"]
    i = 0
    while True:
        oled.fill(0)
        oled_text_wordwrap("MENU DE MENSAJE", 5, 0)
        for j,opcion in enumerate(mensajes):
            if i == j:
                oled.text(">", 0, 16 + 12*j)
            oled.text(opcion,10, 16 + 12*j)
        oled.show()
        
        if not boton_opcion.value():
            i = (i + 1) % len(mensajes)
            time.sleep(0.3)
        if not boton_ok.value():
            if i == 0:
                menu_principal_mensajes()
            elif i == 1:
                menu_receptor_mensajes()
            elif i == 2:
                break
            time.sleep(0.5)
            
def menu_principal_mensajes():
    esperar_soltado(boton_ok)
    opciones = ["Buenos dias", "Buenas tardes", "Hola, Como estas?", "Regresar"]
    i = 0
    while True:
        oled.fill(0)
        oled.text("Opciones de Msj",0,0)
        for j,opcion in enumerate(opciones):
            if i == j:
                oled.text(">",0, 16 + 12*j)
            oled.text(opcion,10, 16 + 12*j)
        oled.show()
        if not boton_opcion.value():
            i = (i + 1) % len(opciones)
            time.sleep(0.3)
            
        if not boton_ok.value():
            if i == 0:
               mostrar_mensaje("Buenos Dias")
            elif i == 1:
                mostrar_mensaje("Buenas tardes")
            elif i == 2:
                mostrar_mensaje("Hola, Como estas?")
            elif i == 3:
                break
            time.sleep(0.5)

def menu_receptor_mensajes():
    esperar_soltado(boton_ok)
    if not macs_nodos:
        oled.fill(0)
        oled.text("No hay nodos", 0, 0)
        oled.text("registrados.", 0, 10)
        oled.show()
        time.sleep(2)
        return
    opciones_msj = ["Buenos dias", "Buenas tardes", "Hola, Como estas?", "Regresar"]
    i = 0
    while True:
        oled.fill(0)
        oled.text("Msj para Receptor", 0, 0)
        for j, opcion in enumerate(opciones_msj):
            if i == j:
                oled.text(">", 0, 16 + 12*j)
            oled.text(opcion, 10, 16 + 12*j)
        oled.show()

        if not boton_opcion.value():
            i = (i + 1) % len(opciones_msj)
            time.sleep(0.3)
        if not boton_ok.value():
            esperar_soltado(boton_ok)
            if i == len(opciones_msj) - 1:
                break
            else:
                nodo_seleccionado = seleccionar_nodo()
                if nodo_seleccionado is not None:
                    mensaje = opciones_msj[i]
                    espnow_interface.send(macs_nodos[nodo_seleccionado], mensaje)
                    oled.fill(0)
                    oled.text("Enviado",0,0)
                    oled.show()
                    time.sleep(1.5)
            time.sleep(0.5)


def mostrar_mensaje(texto):
    oled.fill(0)
    oled_text_wordwrap(texto, 0, 30)
    oled.show()
    activar_buzzer()
    time.sleep(3)

def menu_medicion():
    esperar_soltado(boton_ok)
    opciones = ["Iniciar", "Detener", "Regresar"]
    i = 0
    while True:
        oled.fill(0)
        oled.text("MENU DE SENSOR", 5, 0)
        oled.text("DE GAS",38,10)
        for j,opcion in enumerate(opciones):
            if i == j:
                oled.text(">", 0, 25 + 10*j)
            oled.text(opcion,10,25 + 10*j)
        oled.show()
        
        if not boton_opcion.value():
            i = (i + 1) % len(opciones)
            time.sleep(0.3)
            
        if not boton_ok.value():
            esperar_soltado(boton_ok)
            if i == 0:
                nodo = seleccionar_nodo()
                if nodo is not None:
                    if nodo == len(macs_nodos):
                        for mac in mac_nodos:
                            espnow_interface.send(mac, "MEDIR")
                    else:
                        espnow_interfase.send(macs_nodos[nodo], "MEDIR")
            elif i == 1:
                nodo = seleccionar_nodo()
                if nodo is not None:
                    if nodo == len(macs_nodos):
                        for mac in macs_nodos:
                            espnow_interface.send(mac, "DETENER")
                    else:
                        espnow_interface.send(macs_nodos[nodo], "DETENER")
            else:
                break
            time.sleep(0.5)

def entrar_sleep():
    oled.fill(0)
    oled_text_wordwrap("Entrando a Deep Sleep", 0, 20)
    oled.show()
    time.sleep(2)
    oled.fill(0)
    oled_text_wordwrap("Para poder despertar la ESP32",0,0)
    oled_text_wordwrap("oprimir el boton OK",0,30)
    oled.show()
    time.sleep(5)
    oled.write_cmd(0xAE)  # Apaga pantalla
    led.value(0)
    esp32.wake_on_ext0(pin=boton_ok, level=0)  # Botón OK como wakeup
    deepsleep()
    
if reset_cause() == DEEPSLEEP_RESET:
    oled.write_cmd(0xAF)
    oled.fill(0)
    oled_text_wordwrap("He Despertado!", 10, 20)
    oled.text("Pulsa OK para", 10, 45)
    oled.text("continuar", 28,55)
    oled.show()

    while boton_ok.value():  # Esperar a que se pulse
        pass
    while not boton_ok.value():  # Esperar a que se suelte
        pass

def submenu_accion(destino, nodo = None):
    if destino == "Principal":
        opciones = ["Dormir", "Regresar"]
    else:
        opciones = ["Dormir", "Despertar", "Regresar"]

    i = 0
    while True:
        oled.fill(0)
        oled_text_wordwrap("ACCIONES: " + destino, 0, 0)
        for j, opcion in enumerate(opciones):
            if i == j:
                oled.text(">", 0, 26 + 12*j)
            oled_text_wordwrap(opcion, 10, 26 + 12*j)
        oled.show()

        if not boton_opcion.value():
            i = (i + 1) % len(opciones)
            esperar_soltado(boton_opcion)
            time.sleep(0.1)

        if not boton_ok.value():
            esperar_soltado(boton_ok)
            if opciones[i] == "Dormir":
                if destino == "Receptor":
                    if nodo is not None:
                        espnow_interface.send(macs_nodos[nodo], "DORMIR")
                elif destino == "Principal":
                    entrar_sleep()
                elif destino == "Ambos":
                    for mac in macs_nodos:
                        espnow_interface.send(mac, "DORMIR")
                    entrar_sleep()

            elif opciones[i] == "Despertar":
                if destino == "Receptor":
                    if nodo is not None:
                        espnow_interface.send(macs_nodos[nodo], "DESPERTAR")
                elif destino == "Ambos":
                    for mac in macs_nodos:
                        espnow_interface.send(mac, "DESPERTAR")

            elif opciones[i] == "Regresar":
                break

            time.sleep(0.3)


def menu_dormir():
    esperar_soltado(boton_ok)
    
    opciones = ["Receptor", "Principal", "Ambos", "Regresar"]
    i = 0
    while True:
        oled.fill(0)
        oled_text_wordwrap("MENU DE SUENO", 10, 0)
        for j, opcion in enumerate(opciones):
            if i == j:
                oled.text(">", 0, 16 + 12*j)
            oled_text_wordwrap(opcion, 10, 16 + 12*j)
        oled.show()

        if not boton_opcion.value():
            i = (i + 1) % len(opciones)
            esperar_soltado(boton_opcion)
            time.sleep(0.1)

        if not boton_ok.value():
            esperar_soltado(boton_ok)
            if i == 0:
                nodo = seleccionar_nodo()
                if nodo is not None:
                    submenu_accion("Receptor", nodo)
            elif i == 1:
                submenu_accion("Principal")
            elif i == 2:
                submenu_accion("Ambos")
            elif i == 3:
                break
            time.sleep(0.3)

def realizar_medicion():
    valor_adc = sensor.read()
    voltaje = valor_adc / VALOR_MAX_ADC * Volt_REFERENCIA

    if voltaje != 0:
        Rs = (Volt_REFERENCIA * R_CARGA / voltaje) - R_CARGA
        relacion = Rs / R0
        alcohol_ppm = math.pow(10, ((math.log10(relacion) - 0.4) / -0.4))
        benceno_ppm = math.pow(10, ((math.log10(relacion) - 0.5) / -0.5))

        oled.fill(0)
        oled_text_wordwrap("Alcohol: {:.1f} PPM".format(alcohol_ppm), 0, 0)
        oled_text_wordwrap("Benceno: {:.1f} PPM".format(benceno_ppm), 0, 20)

        if alcohol_ppm > lim_ALCOHOL:
            oled.text("ALCOHOL!", 25, 40)
            activar_buzzer()
        elif benceno_ppm > lim_BENCENO:
            oled.text("BENCENO!", 25, 40)
            activar_buzzer()

        oled.show()
    else:
        oled.fill(0)
        oled.text("Error voltaje 0", 0, 20)
        oled.show()

def seleccionar_nodo():
    if not macs_nodos:
        oled.fill(0)
        oled_text_wordwrap("No hay nodos disponibles", 0, 0)
        oled.show()
        time.sleep(2)
        return None
    
    opciones = ["Nodo {}".format(j+1) for j in range(len(macs_nodos))]
    opciones.append("Todos")
    opciones.append("Regresar")                                    
    i = 0
    while True:
        oled.fill(0)
        oled.text("Seleccionar nodo", 0, 0)
        for j, opcion in enumerate(opciones):
            if i == j:
                oled.text(">", 0, 16 + 10*j)
            oled.text(opcion, 10, 16 + 10*j)
        oled.show()
        
        if not boton_opcion.value():
            i = (i + 1) % len(opciones)
            time.sleep(0.3)
            
        if not boton_ok.value():
            esperar_soltado(boton_ok)
            if opciones[i] == "Regresar":
                return None
            else:
                return i

def modo_receptor():
    oled.fill(0)
    oled_text_wordwrap("Modo Receptor ON", 0, 20)
    oled.show()
    time.sleep(2)

    medicion_activa = False
    ultima_medicion = time.ticks_ms()
    dormido = False

    while True:
        res = espnow_interface.recv()
        if res:
            host, msg = res
            if msg:
                texto = msg.decode()
                mac_str = ":".join("{:02X}".format(b) for b in host)

                if texto == "DORMIR":
                    oled.fill(0)
                    oled_text_wordwrap("De: {}".format(mac_str), 0, 0)
                    oled_text_wordwrap("Me mandaron a dormir", 0, 16)
                    oled.show()
                    time.sleep(4)
                    oled.write_cmd(0xAE)
                    dormido = True

                elif texto == "DESPERTAR":
                    oled.write_cmd(0xAF)
                    oled.fill(0)
                    oled_text_wordwrap("Estoy Despertando...", 0, 30)
                    oled.show()
                    time.sleep(2)
                    oled.fill(0)
                    oled_text_wordwrap("Estoy listo para trabajar de nuevo", 0, 22)
                    oled.show()
                    time.sleep(4)
                    dormido = False

                elif texto == "LED ON":
                    led.value(1)
                    oled.fill(0)
                    oled_text_wordwrap("De: {}".format(mac_str), 0, 0)
                    oled_text_wordwrap("LED ON", 0, 16)
                    oled.show()
                    time.sleep(1)

                elif texto == "LED OFF":
                    led.value(0)
                    oled.fill(0)
                    oled_text_wordwrap("De: {}".format(mac_str), 0, 0)
                    oled_text_wordwrap("LED OFF", 0, 16)
                    oled.show()
                    time.sleep(1)

                elif texto == "MEDIR":
                    medicion_activa = True
                    oled.fill(0)
                    oled_text_wordwrap("De : {}".format(mac_str),0,0)
                    oled_text_wordwrap("Iniciando medicion", 0, 20)
                    oled.show()
                    time.sleep(1)

                elif texto == "DETENER":
                    medicion_activa = False
                    oled.fill(0)
                    oled_text_wordwrap("De : {}".format(mac_str),0,0)
                    oled_text_wordwrap("Medicion detenida", 0, 20)
                    oled.show()
                    time.sleep(1)

                else:
                    oled.fill(0)
                    oled_text_wordwrap("De : {}".format(mac_str),0,0)
                    oled_text_wordwrap("Recibido:", 0, 0)
                    oled_text_wordwrap(texto, 0, 16)
                    oled.show()
                    time.sleep(2)

                time.sleep(0.2)

        if dormido:
            led.value(0)
            continue

        if medicion_activa and time.ticks_diff(time.ticks_ms(), ultima_medicion) >= 1000:
            ultima_medicion = time.ticks_ms()
            realizar_medicion()



# --- PROGRAMA PRINCIPAL --
seleccionar_rol()
esp32personal = b'\xcc\xdb\xa7\x94\x93\xcc'
esp32paratodos = b'\xcc\xdb\xa7\x4f\xd9\xfc'
macs_nodos = [
    esp32personal, #nodo 1
    esp32proyecto #nodo 2
]

if es_principal:
    espnow_interface.add_peer(macs_nodos[0])
    modo_principal()
else:
    R0 = calibrar_mq135()
    if R0:
        modo_receptor()




