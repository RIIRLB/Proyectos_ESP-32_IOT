# Central B 

from machine import Pin, SPI
import network, espnow, ubinascii, time
import st7789

# Pantalla
spi = SPI(1, baudrate=20000000, polarity=0, phase=0,
          sck=Pin(18), mosi=Pin(19))
display = st7789.Display(spi, Pin(16, Pin.OUT), Pin(5, Pin.OUT),
                              Pin(23, Pin.OUT), Pin(4, Pin.OUT),
                              rotation=1)

# Botones 
class Boton:
    def __init__(self, pin):
        self.pin  = Pin(pin, Pin.IN, Pin.PULL_UP)
        self.ultimo = 0
    def presionado(self):
        ahora = time.ticks_ms()
        if self.pin.value() == 0 and time.ticks_diff(ahora, self.ultimo) > 300:
            self.ultimo = ahora
            return True
        return False

btn_confirmar = Boton(0)
btn_navegar   = Boton(35)

# ESP-NOW
sta = network.WLAN(network.STA_IF)
sta.active(True)
e = espnow.ESPNow()
e.active(True)

MAC_A = ubinascii.unhexlify('ac15186f7ccc')
MAC_C = ubinascii.unhexlify('ac15186f790c')
MAC_D = ubinascii.unhexlify('ac15186f8124')
MAC_E = ubinascii.unhexlify('ac15186f7c14')

e.add_peer(MAC_A); e.add_peer(MAC_C)
e.add_peer(MAC_D); e.add_peer(MAC_E)

# Estado de nodos
nodos = {
    "A": {"datos": None, "ultimo": 0, "activo": False, "mac": MAC_A, "dormido": False},
    "C": {"datos": None, "ultimo": 0, "activo": False, "mac": MAC_C, "dormido": False},
    "D": {"datos": None, "ultimo": 0, "activo": False, "mac": MAC_D, "dormido": False},
    "E": {"datos": None, "ultimo": 0, "activo": False, "mac": MAC_E, "dormido": False},
}

TIMEOUT    = 15000
NODOS_MENU = list(nodos.keys()) + ["Todos"]

colores = {
    "A": st7789.YELLOW,
    "C": st7789.MAGENTA,
    "D": st7789.GREEN,
    "E": st7789.CYAN,
    "Todos": st7789.WHITE,
}

INTERVALO_CICLO_MS = 5000
ultimo_ciclo_ms    = 0
pagina_monitor     = 0

MODO_MONITOR = 0; MODO_NIVEL1 = 1; MODO_NIVEL2 = 2; MODO_DETALLE = 3

modo = MODO_MONITOR; opcion_n1 = 0; opcion_n2 = 0
nodo_activo = None; nodo_detalle = None; tiempo_detalle = 0
pantalla_sucia = True; ultimo_render = 0; INTERVALO_MS = 1000

# Helpers de nodos
def get_nodos_visibles():
    ahora = time.ticks_ms()
    return [n for n, info in nodos.items()
            if (info["activo"] and time.ticks_diff(ahora, info["ultimo"]) <= TIMEOUT)
            or info["dormido"]]

def procesar_mensaje(origen, partes):
    try:
        ax,ay,az   = float(partes[1]),float(partes[2]),float(partes[3])
        temp_amb   = float(partes[4]); temp_obj = float(partes[5])
        temp_dht   = int(partes[6]);   hum_dht  = int(partes[7])
        valor_mq   = int(partes[8]);   pct_mq   = float(partes[9])
        return {
            "mpu": (ax,ay,az)          if not (ax==0.0 and ay==0.0 and az==0.0) else None,
            "gy":  (temp_amb,temp_obj) if temp_amb != -1.0 else None,
            "dht": (temp_dht,hum_dht)  if temp_dht != -1   else None,
            "mq":  (valor_mq,pct_mq)   if valor_mq != -1   else None,
        }
    except:
        return None

def _resumen_corto(datos):
    lineas = []
    if datos.get("mpu"):
        ax,ay,az = datos["mpu"]
        mov = abs(az-1.0)>0.3 or abs(ax)>0.3 or abs(ay)>0.3
        lineas.append("MPU X:{:.2f} Z:{:.2f}".format(ax,az))
        lineas.append("MOV!" if mov else "Reposo")
    if datos.get("gy"):
        amb,obj = datos["gy"]
        lineas.append("GY {:.1f}/{:.1f}C".format(amb,obj))
        lineas.append("FIEBRE!" if obj>=38 else "Normal")
    if datos.get("dht"):
        t,h = datos["dht"]
        ok = 18<=t<=26 and 30<=h<=60
        lineas.append("DHT {}C {}%".format(t,h))
        lineas.append("OK" if ok else "Fuera rango")
    if datos.get("mq"):
        v,pct = datos["mq"]
        cal = "Limpio" if v<1200 else "Regular" if v<2000 else "Malo" if v<2500 else "Peligroso"
        lineas.append("MQ:{} {}".format(v,cal))
    return lineas or ["Sin sensores"]

def _acciones_para(nombre):
    if nombre == "Todos":
        return ["Solicitar todos","Dormir todos","Activar todos","Volver"]
    return ["Activar","Volver"] if nodos[nombre]["dormido"] else ["Solicitar medicion","Dormir","Volver"]

# Pantallas
def pantalla_monitor():
    global pagina_monitor
    display.fb.fill(st7789.BLACK)
    display.header([
        {"txt": "Central B",        "color": st7789.YELLOW},
        {"txt": "P35:sig P0:menu",  "color": st7789.WHITE, "align": "right"},
    ])

    visibles = get_nodos_visibles()
    total    = len(visibles)

    if total == 0:
        display.cursor(x=0, y=14)
        display.print("ESPERA",            st7789.WHITE, scale=2, align="center", max_w=display.width)
        display.print("Sin nodos activos", st7789.RED,   align="center", max_w=display.width)
        display.show(); return

    if pagina_monitor >= total: pagina_monitor = 0
    nombre    = visibles[pagina_monitor]
    info      = nodos[nombre]
    color     = colores.get(nombre, st7789.WHITE)
    ahora     = time.ticks_ms()
    sin_datos = time.ticks_diff(ahora, info["ultimo"]) > TIMEOUT

    display.cursor(x=2, y=14)
    display.print("Nodo "+nombre, color, scale=2)

    if info["dormido"]:
        display.print("ZZZ Dormido", st7789.RED, scale=2)
    elif not sin_datos and info["datos"]:
        for linea in _resumen_corto(info["datos"])[:6]:
            display.print(linea, st7789.WHITE)
    else:
        display.print("Sin datos recientes", st7789.RED)

    if total > 1:
        transcurrido = time.ticks_diff(ahora, ultimo_ciclo_ms)
        seg = max(0, (INTERVALO_CICLO_MS - transcurrido) // 1000)
        pag_txt = "{}/{} {}s".format(pagina_monitor+1, total, seg)
    else:
        pag_txt = "1/1"
    display.print(pag_txt, st7789.WHITE, x=0, y=display.height-10,
                  align="right", max_w=display.width)
    display.show()


def pantalla_nivel1():
    visibles = get_nodos_visibles()
    display.fb.fill(st7789.BLACK)
    display.header([
        {"txt": "Elegir nodo:",   "color": st7789.CYAN},
        {"txt": "P35:nav P0:ok", "color": st7789.WHITE, "align": "right"},
    ])
    display.cursor(x=5, y=12)
    for i, nombre in enumerate(NODOS_MENU):
        color = colores.get(nombre, st7789.WHITE)
        if nombre != "Todos":
            info   = nodos[nombre]
            estado = " [ZZZ]" if info["dormido"] else (" [OK]" if nombre in visibles else " [---]")
        else:
            estado = " ({})".format(len(visibles))
        prefijo = "> " if i == opcion_n1 else "  "
        display.print(prefijo+"Nodo "+nombre+estado,
                      st7789.YELLOW if i == opcion_n1 else color)
    display.show()


def pantalla_nivel2(nombre):
    display.fb.fill(st7789.BLACK)
    color  = colores.get(nombre, st7789.WHITE)
    titulo = "Todos" if nombre=="Todos" else "Nodo "+nombre
    display.header([
        {"txt": titulo,          "color": color},
        {"txt": "P35:nav P0:ok", "color": st7789.WHITE, "align": "right"},
    ])
    acciones = _acciones_para(nombre)
    display.cursor(x=10, y=12)
    for i, accion in enumerate(acciones):
        prefijo = "> " if i == opcion_n2 else "  "
        display.print(prefijo+accion, st7789.YELLOW if i==opcion_n2 else st7789.WHITE)
    display.show()


def pantalla_detalle(nombre):
    info  = nodos[nombre]
    datos = info["datos"]
    color = colores.get(nombre, st7789.WHITE)
    display.fb.fill(st7789.BLACK)
    display.cursor(x=5, y=2)
    display.print("Nodo "+nombre, color, scale=2)

    if not datos:
        display.print("Sin datos aun", st7789.RED)
    else:
        if datos.get("mpu"):
            ax,ay,az = datos["mpu"]
            mov = abs(az-1.0)>0.3 or abs(ax)>0.3 or abs(ay)>0.3
            display.print("MPU-6050", st7789.YELLOW)
            display.print("X:{:.2f} Y:{:.2f} Z:{:.2f}".format(ax,ay,az), st7789.WHITE)
            display.print("MOV!" if mov else "Reposo", st7789.RED if mov else st7789.GREEN)
        if datos.get("gy"):
            amb,obj = datos["gy"]
            fiebre = obj>=38
            display.print("GY-906", st7789.MAGENTA)
            display.print(("Amb:{:.1f}  ".format(amb), st7789.WHITE),
                          ("Obj:{:.1f}C".format(obj),  st7789.RED if fiebre else st7789.WHITE))
            display.print("FIEBRE!" if fiebre else "Normal", st7789.RED if fiebre else st7789.GREEN)
        if datos.get("dht"):
            t,h = datos["dht"]
            ok = 18<=t<=26 and 30<=h<=60
            display.print("DHT11", st7789.GREEN)
            display.print(("{}C  ".format(t), st7789.WHITE), ("{}%".format(h), st7789.WHITE))
            display.print("OK" if ok else "Fuera rango", st7789.GREEN if ok else st7789.RED)
        if datos.get("mq"):
            v,pct = datos["mq"]
            cal,c2 = (("Limpio",    st7789.GREEN)  if v<1200 else
                      ("Regular",   st7789.YELLOW) if v<2000 else
                      ("Malo",      st7789.RED)    if v<2500 else
                      ("Peligroso", st7789.RED))
            display.print("MQ-135", st7789.CYAN)
            display.print(("ADC:{}  ".format(v), st7789.WHITE), ("{:.1f}%".format(pct), st7789.WHITE))
            display.print(cal, c2)
        if not any(datos.get(k) for k in ("mpu","gy","dht","mq")):
            display.print("Sin sensores activos", st7789.RED)

    restante = 30-(time.ticks_diff(time.ticks_ms(),tiempo_detalle)//1000)
    display.print("Vuelve en {}s".format(max(0,restante)), st7789.WHITE, x=5, y=118)
    display.print("P0: salir ya", st7789.WHITE, x=0, y=display.height-10,
                  align="center", max_w=display.width)
    display.show()

# Enviar comandos
def enviar_comando(accion, nombre):
    if accion == "Solicitar medicion":
        e.send(nodos[nombre]["mac"], b'SOLICITUD')
    elif accion == "Solicitar todos":
        for n,info in nodos.items(): e.send(info["mac"], b'SOLICITUD')
    elif accion == "Dormir":
        e.send(nodos[nombre]["mac"], b'DORMIR'); nodos[nombre]["dormido"]=True
    elif accion == "Dormir todos":
        for n,info in nodos.items():
            e.send(info["mac"], b'DORMIR'); nodos[n]["dormido"]=True
    elif accion == "Activar":
        display.fb.fill(st7789.BLACK)
        display.cursor(x=0, y=40)
        display.print("Activando",  st7789.CYAN,  scale=2, align="center", max_w=display.width)
        display.print(nombre,       st7789.WHITE, scale=2, align="center", max_w=display.width)
        display.print("Espera 35s", st7789.WHITE,          align="center", max_w=display.width)
        display.show()
        inicio=time.ticks_ms(); activado=False
        while time.ticks_diff(time.ticks_ms(),inicio) < 35000:
            e.send(nodos[nombre]["mac"], b'ACTIVAR')
            sender,resp = e.recv(200)
            if resp and resp == b'ACK:ACTIVAR': activado=True; break
            time.sleep_ms(500)
        nodos[nombre]["dormido"] = False
        display.fb.fill(st7789.BLACK)
        display.cursor(x=0, y=50)
        if activado:
            display.print("OK!", st7789.GREEN, scale=3, align="center", max_w=display.width)
            display.print(nombre+" activado!", st7789.GREEN, align="center", max_w=display.width)
        else:
            display.cursor(x=5, y=60)
            display.print("Cmd enviado.\nSin confirmacion.", st7789.YELLOW, wrap=True)
        display.show(); time.sleep(1 if activado else 2)
    elif accion == "Activar todos":
        display.fb.fill(st7789.BLACK); display.cursor(x=0, y=40)
        display.print("Activando todos", st7789.CYAN,  scale=2, align="center", max_w=display.width)
        display.print("Espera 35s",      st7789.WHITE,           align="center", max_w=display.width)
        display.show()
        inicio=time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(),inicio) < 35000:
            for n,info in nodos.items():
                if nodos[n]["dormido"]: e.send(info["mac"], b'ACTIVAR')
            time.sleep_ms(500)
        for n in nodos: nodos[n]["dormido"]=False

# Pantalla de arranque
display.fb.fill(st7789.BLACK); display.cursor(x=0, y=45)
display.print("Central B",    st7789.CYAN,  scale=2, align="center", max_w=display.width)
display.print("Iniciando...", st7789.WHITE,           align="center", max_w=display.width)
display.show(); time.sleep(1)

# Bucle principal
while True:
    sender,msg = e.recv(100)
    if msg:
        try:
            texto=msg.decode(); partes=texto.split(":"); origen=partes[0]
            if origen == "ACK":
                cmd = partes[1] if len(partes)>1 else "?"
                for n,info in nodos.items():
                    if info["mac"]==sender:
                        nodos[n]["dormido"]=(cmd=="DORMIR"); break
            elif origen == "DORMIDO":
                for n,info in nodos.items():
                    if info["mac"]==sender: nodos[n]["dormido"]=True; break
            elif origen in nodos:
                datos=procesar_mensaje(origen,partes)
                if datos:
                    nodos[origen].update({"datos":datos,"ultimo":time.ticks_ms(),"activo":True,"dormido":False})
                    pantalla_sucia=True
                    if modo==MODO_DETALLE and nodo_detalle==origen: pantalla_detalle(origen)
        except Exception as err:
            print("Error:",err)

    if btn_confirmar.presionado():
        if modo==MODO_MONITOR:
            modo=MODO_NIVEL1; opcion_n1=0; pantalla_nivel1()
        elif modo==MODO_NIVEL1:
            nodo_activo=NODOS_MENU[opcion_n1]; opcion_n2=0
            modo=MODO_NIVEL2; pantalla_nivel2(nodo_activo)
        elif modo==MODO_NIVEL2:
            accion=_acciones_para(nodo_activo)[opcion_n2]
            if accion=="Volver":
                modo=MODO_NIVEL1; opcion_n2=0; pantalla_nivel1()
            elif accion in ("Solicitar medicion","Solicitar todos"):
                enviar_comando(accion,nodo_activo)
                if nodo_activo!="Todos":
                    nodo_detalle=nodo_activo; tiempo_detalle=time.ticks_ms()
                    modo=MODO_DETALLE; time.sleep_ms(500); pantalla_detalle(nodo_activo)
                else:
                    modo=MODO_MONITOR; pantalla_sucia=True
            else:
                enviar_comando(accion,nodo_activo); modo=MODO_MONITOR; pantalla_sucia=True
        elif modo==MODO_DETALLE:
            modo=MODO_MONITOR; pantalla_sucia=True

    if btn_navegar.presionado():
        if modo==MODO_MONITOR:
            visibles=get_nodos_visibles()
            if len(visibles)>1: pagina_monitor=(pagina_monitor+1)%len(visibles)
            ultimo_ciclo_ms=time.ticks_ms(); pantalla_sucia=True
        elif modo==MODO_NIVEL1:
            opcion_n1=(opcion_n1+1)%len(NODOS_MENU); pantalla_nivel1()
        elif modo==MODO_NIVEL2:
            acciones=_acciones_para(nodo_activo)
            opcion_n2=(opcion_n2+1)%len(acciones); pantalla_nivel2(nodo_activo)

    ahora_ms=time.ticks_ms()
    if modo==MODO_MONITOR:
        if time.ticks_diff(ahora_ms,ultimo_ciclo_ms)>=INTERVALO_CICLO_MS:
            visibles=get_nodos_visibles()
            if len(visibles)>1: pagina_monitor=(pagina_monitor+1)%len(visibles)
            ultimo_ciclo_ms=ahora_ms; pantalla_sucia=True
        if pantalla_sucia or time.ticks_diff(ahora_ms,ultimo_render)>=INTERVALO_MS:
            pantalla_monitor(); pantalla_sucia=False; ultimo_render=ahora_ms
    elif modo==MODO_DETALLE:
        if time.ticks_diff(ahora_ms,tiempo_detalle)>=30000:
            modo=MODO_MONITOR; pantalla_sucia=True
        else:
            pantalla_detalle(nodo_detalle)

    time.sleep_ms(50)
