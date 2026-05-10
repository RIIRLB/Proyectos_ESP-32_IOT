# Biblioteca de prestaciones
from machine import Timer

# Estados
class estado:
    
    def __init__(self, nombre_estado, funcion_salida):
        # Nombre del estado
        self._nombre = nombre_estado
        # Función de salida (Moore) ...
        # la función que se dé de alta debe recibir el nombre del estado
        self._salida = funcion_salida
        
    def ejecuta_salida(self):
        # Ejecuta la función de salida, pasando el nombre del estado
        self._salida(self._nombre)
        
# Autómata
class automata:
    
    def __init__(self, estados, inicial, entradas, retardo=3):
        # Lista de estados
        self._estados = estados
        # Diccionario de transiciones
        self._transiciones = dict()
        # Estado  inicial (reset)
        self._estado_inicial = inicial
        self._estado_actual = inicial
        self._estado_actual.ejecuta_salida()
        # Funcion para lectura de entradas
        self._entradas = entradas
        # Temporizador para cambio de estado
        self._timer = Timer(0)
        # Retardo de transición
        self._retardo = retardo*1000
        
    def agrega_transicion(self, desde, entradas, hacia):
        # Si hay entradas 'sin importancia' en la transición,
        # llena el diccionario con todas las opciones
        if 'x' in entradas:
            # Localiza las entradas sin importancia
            inxx = [i for i, e in enumerate(entradas) if e == 'x']
            # Convierte cadena de entradas a lista por facilidad
            le = list(entradas)
            # Para cada posible combinación de entradas sin importancia
            for c in range(2**len(inxx)):
                sust = f'{c:0{len(inxx)}b}'
                # Realiza las sustituciones y agrega la transición al diccionario
                for pos, val in zip(inxx, sust):
                    le[pos] = val
                    self._transiciones[(desde, ''.join(le))] = hacia
        else:
            # Solo se agregó una transición con condiciones plenas
            self._transiciones[(desde, entradas)] = hacia
                    
    def _transicion(self, timer):
        # Cambio de estado
        nombre_edo_siguiente = self._transiciones.get((self._estado_actual._nombre,
                                                       self._entradas()),
                                                      self._estado_actual._nombre)
        self._estado_actual = [edo for edo in self._estados
                               if edo._nombre == nombre_edo_siguiente][0]
        # Función de salida del estado actual
        self._estado_actual.ejecuta_salida()
        # Prepara siguiente transición, reiniciando el timer
        self._timer.init(period=self._retardo, mode=Timer.ONE_SHOT,
                         callback=self._transicion)
        
    def init(self):
        # Estado  inicial (reset)
        self._estado_actual = self._estado_inicial
        self._estado_actual.ejecuta_salida()
    	# Arranca el timer para provocar la siguiente transición
        self._timer.init(period=self._retardo, mode=Timer.ONE_SHOT,
                         callback=self._transicion)
        

    
    


















            


