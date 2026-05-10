# Bibliotecas con las clases relevantes
from automata_sym import estado, automata
from machine import Pin

# Función de salida
def salida(nombre):
    # Valores para el led RGB
    rgb = ['111', '111', '110', '101',
           '100', '011', '010', '001', '000']
    # Ubica el estado dado el nombre
    pos = nombres.index(nombre)
    # Establece las salidas
    for led, val in zip(leds, rgb[pos]):
        led.value(val == '1')
    # Muestra salidas
    #print(f'{nombre:10} ({pos}): {rgb[pos]}')

# Función de lectura de entradas
def lee_entradas():
    return f'{switches[0].value()}{switches[1].value()}'

# Hardware 
leds = (Pin(14, Pin.OUT), Pin(27, Pin.OUT), Pin(26, Pin.OUT))
switches = (Pin(12, Pin.IN, Pin.PULL_UP), Pin(13, Pin.IN, Pin.PULL_UP))

# Nombres de los estados
nombres = ['off', 'negro', 'azul', 'verde', 'cyan',
           'rojo', 'magenta', 'amarillo', 'blanco']
# Crea lista de estados
estados = [estado(edo, salida) for edo in nombres]

# Crea autómata
aef = automata(estados, estados[0], lee_entradas, retardo=1)
# Agrega transiciones
aef.agrega_transicion('off', '1x', 'cyan')
aef.agrega_transicion('off', '0x', 'verde')
for i in range(1, 9):
    aef.agrega_transicion(nombres[i], 'x0', nombres[max(1, (i+1)%9)])
    aef.agrega_transicion(nombres[max(1, (i+1)%9)], 'x1', nombres[i])

# Ejecuta autómata
aef.init()    