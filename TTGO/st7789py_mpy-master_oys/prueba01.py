import gc
import time

import comfortaa_24 as font
import tft_config
import st7789py as st7789

gc.collect()

if __name__ == '__main__':
    tft = tft_config.config(tft_config.WIDE)
    colors = [st7789.GREEN, st7789.YELLOW, st7789.RED]
    mensajes = ['ayúdame', 'por favor', 'llama al', '5523367304']
    i = 0
    try:
        while True:
            row = 0
            for msj in mensajes:
                tft.write(font, msj, (tft.physical_height-tft.write_width(font, msj))//2, row, colors[i])
                row += font.HEIGHT
            time.sleep(0.3)
            i = (i + 1) % len(colors)
    except KeyboardInterrupt:
        pass
