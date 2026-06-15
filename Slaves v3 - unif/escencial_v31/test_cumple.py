# test_cumple.py — SMOKE-TEST de la TTGO usando los módulos del slave.
#
# Verifica de un golpe: arranque, config.py, ui.py (display + botones +
# backlight) y las fuentes Comfortaa, pintando un mensaje de cumpleaños
# con efectos (arcoíris, confeti, borde pulsante).
#
# Subir junto con: config.py, ui.py, tft_config.py, st7789py.py,
# comfortaa_16.py, comfortaa_24.py. Luego córrelo (en el REPL: 
# `import test_cumple`) o renómbralo a main.py para que arranque solo.
#
# Solo usa métodos que tu driver st7789py ya expone:
#   tft.write(font, texto, x, y, color) · tft.fill(color)
#   tft.fill_rect(x, y, w, h, color)    · tft.write_width(font, texto)
#   tft.physical_height / tft.physical_width

import time
try:
    import urandom as random
except ImportError:
    import random
import config        # noqa: F401  (se importa para verificar que carga)
import ui

# ── color RGB565 inline (sin depender del color565 del driver) ──
def rgb(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

def wheel(p):                     # rueda de color 0..255 -> rgb565 vivo
    p &= 255
    if p < 85:
        return rgb(255 - p * 3, p * 3, 0)
    if p < 170:
        p -= 85
        return rgb(0, 255 - p * 3, p * 3)
    p -= 170
    return rgb(p * 3, 0, 255 - p * 3)

NEG = 0x0000


def main():
    tft = ui.init(rotation=1)     # inicializa display + botones + backlight
    ui.backlight_on()
    sm, md = ui.sm, ui.md
    W = tft.physical_height       # 240 en horizontal (convención del slave)
    H = tft.physical_width        # 135

    def cx(font, txt):            # x para centrar horizontalmente
        return max(0, (W - tft.write_width(font, txt)) // 2)

    # La fuente Comfortaa SÍ incluye la 'ñ' (MAP hasta 0xFB), así que el
    # mensaje va completo y bonito.
    lines = [
        (md, "Feliz",                  8),
        (md, "cumpleaños",            36),
        (md, "Ale!!!",                64),
        (sm, "espero que te la pases", 96),
        (sm, "muy bonito :3",         114),
    ]

    print("[TEST] cumple Ale -- display {}x{} | fuentes OK | efectos ON".format(W, H))
    tft.fill(NEG)
    f = 0
    while True:
        f += 1
        # limpiar cada cierto tiempo para que el confeti no sature
        if f % 14 == 0:
            tft.fill(NEG)

        # confeti
        for _ in range(10):
            x = random.getrandbits(8) % (W - 3)
            y = random.getrandbits(8) % (H - 3)
            tft.fill_rect(x, y, 3, 3, wheel(random.getrandbits(8)))

        # borde pulsante
        bc = wheel(f * 6)
        tft.fill_rect(0, 0, W, 2, bc)
        tft.fill_rect(0, H - 2, W, 2, bc)
        tft.fill_rect(0, 0, 2, H, bc)
        tft.fill_rect(W - 2, 0, 2, H, bc)

        # texto arcoíris: se redibuja en su sitio; el fondo negro de cada
        # glifo borra el confeti que haya caído encima -> texto siempre limpio
        for i, (font, txt, y) in enumerate(lines):
            tft.write(font, txt, cx(font, txt), y, wheel(f * 6 + i * 40))

        time.sleep_ms(70)


main()
