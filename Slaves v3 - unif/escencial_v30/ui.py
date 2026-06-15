# ui.py — capa visual completa del slave.
#
# Absorbe lo que el doc separaba en fonts.py y display.py:
#   - fuentes (reexport de Comfortaa)
#   - init de pantalla + botones + backlight
#   - pantallas (rotativas, alerta, ON/OFF)  -> se llenan en el PASO 5
#
# Agnóstico al driver: usa tft_config.config(), que hoy devuelve st7789py.
# La decisión "st7789py actual vs st7789.py de Ana" se cierra en el paso 5;
# este módulo no cambia cuando se decida, solo lo hace tft_config.

from machine import Pin
import tft_config
import config

# ───────────────────────────────────────────────
#  FUENTES  (antes fonts.py / fuentes.py de Ana)
#  Requiere comfortaa_16.py y comfortaa_24.py en el dispositivo.
#  No usamos "import *" porque ambos módulos definen los mismos nombres
#  y el segundo pisaría al primero; como módulos preservamos las dos.
# ───────────────────────────────────────────────
import comfortaa_16 as sm   # fuente pequeña
import comfortaa_24 as md   # fuente mediana

# ───────────────────────────────────────────────
#  ESTADO DEL MÓDULO  (variables globales, sección 5.2)
# ───────────────────────────────────────────────
tft       = None
backlight = None
btn_izq   = None
btn_der   = None


# ───────────────────────────────────────────────
#  INIT DE HARDWARE VISUAL + INPUT  (antes display.init)
# ───────────────────────────────────────────────
def init(rotation=1):
    """Inicializa pantalla + botones + backlight. Devuelve el objeto tft.

    El backlight arranca APAGADO: el modo híbrido lo prende solo en eventos.
    """
    global tft, backlight, btn_izq, btn_der
    tft = tft_config.config(rotation=rotation)

    backlight = Pin(config.PIN_BACKLIGHT, Pin.OUT)
    backlight.value(0)

    btn_izq = Pin(config.PIN_BTN_LEFT,  Pin.IN, Pin.PULL_UP)
    btn_der = Pin(config.PIN_BTN_RIGHT, Pin.IN, Pin.PULL_UP)
    return tft


def backlight_on():
    if backlight:
        backlight.value(1)


def backlight_off():
    if backlight:
        backlight.value(0)


# ───────────────────────────────────────────────
#  PANTALLAS  (STUB — se implementan en el PASO 5)
# ───────────────────────────────────────────────
def boot(node_id):
    """Pantalla de arranque. Best-effort: no debe tumbar el boot."""
    if not tft:
        return
    try:
        tft.fill(0x0000)
        # API tentativa del driver actual (st7789py): write(font, txt, x, y, color)
        tft.write(sm, "PIF NODE", 4, 4, 0x07E0)
        tft.write(sm, node_id, 4, 28, 0xFFFF)
    except Exception as e:
        print("[UI] boot best-effort:", e)
    # TODO paso 5: layout real, centrado, header()


def mostrar_normal(lec):
    pass    # TODO paso 5


def mostrar_alerta(nivel, sensores, lec):
    pass    # TODO paso 5


def rotar(lec):
    pass    # TODO paso 5 (cambia de vista cada ~3 s si hay varios sensores)
