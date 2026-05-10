import st7789py as st7789
from machine import Pin, SPI
import time

# Configura SPI para TTGO T-Display
spi = SPI(1, baudrate=40000000, sck=Pin(18), mosi=Pin(19))

# Pines de control
dc  = Pin(16, Pin.OUT)
rst = Pin(23, Pin.OUT)
cs  = Pin(5, Pin.OUT)

# Crea el objeto de pantalla
tft = st7789.ST7789(
    spi, 135, 240,               # Tamaño del display (alto x ancho)
    reset=rst,
    cs=cs,
    dc=dc,
    rotation=1                  # 0: vertical, 1: horizontal (rotación)
)

# Inicializa la pantalla
tft.init()

# Muestra texto
colores = [st7789.RED, st7789.GREEN, st7789.BLUE, st7789.YELLOW, st7789.CYAN]
mensajes = ["Hola!", "TTGO T-Display", "MicroPython", "Pantalla ST7789", "Funciona :)"]

while True:
    for i in range(len(mensajes)):
        tft.fill(st7789.BLACK)
        tft.text(
            font=None,                      # Usa fuente por defecto
            string=mensajes[i],
            x=20, y=60,
            color=colores[i % len(colores)]
        )
        time.sleep(1)
