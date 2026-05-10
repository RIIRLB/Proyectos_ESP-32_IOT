from machine import Pin, Timer

class TecladoMatricial():
    
    def __init__(self, indices_renglones = [], indices_columnas = [], teclas = ''):
        # Configuración del hardware
        self._renglones = [Pin(indice, Pin.OUT) for indice in indices_renglones]
        self._columnas = [Pin(indice, Pin.IN, Pin.PULL_UP) for indice in indices_columnas]
        [pin.irq(trigger=Pin.IRQ_FALLING, handler=self._tecla_oprimida) for pin in self._columnas]
        # Teclas
        self._teclas = teclas
        # Ultima tecla oprimida
        self._tecla = ''
        # Patrones de excitación para los renglones
        self._salidas = [[int(j != i) for j in range(len(self._columnas))] for i in range(len(self._renglones))]
        # Indice de la salida actual
        self._indice_salida = 0
        # Timer para escanear el teclado
        self._timer = Timer(0, mode=Timer.PERIODIC, freq=5, callback=self._escanea)
        
    def __del__(self):
        # Detiene el timer
        self._timer.deinit()
        
    def _tecla_oprimida(self, pin):
        # Obtiene la columna oprimida
        indice_entrada = self._columnas.index(pin)
        # Calcula índice de la tecla
        indice = len(self._columnas)*self._indice_salida + indice_entrada
        # Almacena tecla oprimida
        self._tecla = self._teclas[indice]

    def _escanea(self, timer):
        # Si no tengo una tecla almacenada, escaneo
        if self._tecla == '':
            # Avance (circular) al siguiente patrón de renglones
            self._indice_salida = (self._indice_salida + 1) % len(self._salidas)
            # Activa el patrón en los renglones
            [renglon.value(valor) for renglon, valor in zip(self._renglones, self._salidas[self._indice_salida])]
        # Si tengo tecla almacenada, desactivo
        else:
            [renglon.value(1) for renglon in self._renglones]
                
    def lee(self):
        # Prepara la tecla almacenada (o nada) para regresar
        tecla = self._tecla
        # Limpia la tecla almacenada
        self._tecla = ''
        # Regresa la última tecla almacenada
        return tecla
    
# Prueba
if __name__ == '__main__':
    print('Prueba del teclado 4x4')
    mi_teclado = TecladoMatricial(indices_renglones=[14, 27, 26, 25], indices_columnas = [33, 32, 35, 34], teclas = '123A456B789C*0#D')
    try:
        while True:
            if (tecla := mi_teclado.lee()) != '':
                print(tecla)
    except KeyboardInterrupt:
        print('Terminamos')
    