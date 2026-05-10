#Automata de teclado matrcial
from automata_sym import estado, automata
from machine import Pin, Timer

# Hardware 
indice_de_renglones = (Pin(18, Pin.IN), Pin(5, Pin.IN), Pin(17, Pin.IN), Pin(16, Pin.IN))
indice_de_columnas = (Pin(4, Pin.OUT, Pin.PULL_UP), Pin(0, Pin.OUT, Pin.PULL_UP), Pin(2, Pin.OUT, Pin.PULL_UP), Pin(15, Pin.OUT, Pin.PULL_UP))
teclas = '123A456B789C*0#D'


# Función de salida
def salida(nombre):
    # Valores para el led RGB
    salida_renglones = ['0111', '1011', '1101', '1110']
    # Ubica el estado dado el nombre
    pos = nombres.index(nombre)
    # Establece las salidas
    for renglon, val in zip(indice_de_renglones, salida_renglones[pos]):
        renglon.value(val == '1')
    # Muestra salidas
    #print(f'{nombre:10} ({pos}): {rgb[pos]}')


# Función de lectura de entradas
def lee_entradas():
    return ''

# Nombres de los estados
nombres = ['renglon_0', 'renglon_1', 'renglon_2', 'renglon_3']
# Crea lista de estados
estados = [estado(edo, salida) for edo in nombres]


# Crea automata
aef = automata(estados, estados[0], lee_entradas, 3)

# Transiciones
aef.agrega_transicion('renglon_0','','renglon_1')
aef.agrega_transicion('renglon_1','','renglon_2')
aef.agrega_transicion('renglon_2','','renglon_3')
aef.agrega_transicion('renglon_3','','renglon_0')

# Ejecuta autómata
aef.init()

print(aef._transiciones)
while True:
    print(f'{aef._estado_actual._nombre}')
