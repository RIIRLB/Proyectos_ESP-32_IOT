import tft_config
import st7789py as st7789
from print import Print

tft = tft_config.config(rotation=1)
tft.init()
tft.fill(st7789.BLACK)

pantalla = Print(tft, fg=st7789.CYAN)

pantalla("Temperatura:")
pantalla(25.3)
pantalla("¡Funciona!")
