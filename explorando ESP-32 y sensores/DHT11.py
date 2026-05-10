### microproyecto 4 sensor de temperatura y humedad; R
import dht              ### tambien hay una libreria de el componente
from machine import Pin ### siempre vamos a usar este para los pines
import time             ### en este caso vamos a necesitar esto para medir import
                        ### intervalos y que no se sature de informacion

### inicializamos el sensor DHT11 desde el pin 15
sensor = dht.DHT11(Pin(15))

while True:     ### declaramos un bucle
    try:        ### sentencia para el manejo de errores en algun proceso de 
                ### el bloque de codigo que se este ejecutando try-except-finaly
        sensor.measure()    ### se toma una lectura del sensor
        
        temp = sensor.temperature() ###se obtiene temprarutra
        hum = sensor.humidity()     ###se obtiene humedad
        
        ### imprime los valores en °C y la humedad en %
        print("Temperatura: {}°C   Humedad: {}%".format(temp, hum))
        
    ### si es que no esta bien conectado, se mandara el mensaje
    except OSError as e: 
        print("Error al leer el sensor, conectalo bien!:", e)
    
    ### se ahce un tiempo de espera de 2 seg para la siguiente lectura
    time.sleep(1)