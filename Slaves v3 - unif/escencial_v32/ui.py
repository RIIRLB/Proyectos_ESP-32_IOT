# ui.py — capa visual del slave (fuentes + display + pantallas).
#
# Agnóstico al driver: usa tft_config.config(), que hoy devuelve st7789py.
# Métodos del driver usados: write(font,txt,x,y,color), fill(color),
# fill_rect(x,y,w,h,color), write_width(font,txt), physical_height/width.
#
# Colores RGB565 inline (no dependemos de st7789.color565 ni del nombre
# del módulo del driver).

from machine import Pin, SPI
import st7789py as st7789
import config

from fuentes import sm, md     # Comfortaa 16 (sm) y 24 (md), ya fusionadas


def _rgb(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

NEGRO   = 0x0000
BLANCO  = 0xFFFF
VERDE   = 0x07E0
ROJO    = 0xF800
AMARILLO = 0xFFE0
CYAN    = 0x07FF
GRIS    = _rgb(110, 130, 120)
NARANJA = _rgb(255, 140, 0)
AZUL    = _rgb(80, 160, 255)

# ── estado del módulo ──
tft       = None
backlight = None
btn_izq   = None
btn_der   = None
W = H = 0


def init(rotation=1):
    """Inicializa pantalla + botones + backlight. Devuelve el objeto tft.

    Pines del LilyGo T-Display (antes en tft_config.py), aquí fusionados.
    """
    global tft, backlight, btn_izq, btn_der, W, H
    backlight = Pin(config.PIN_BACKLIGHT, Pin.OUT)
    tft = st7789.ST7789(
        SPI(2, baudrate=30000000, sck=Pin(18), mosi=Pin(19), miso=None),
        135, 240,
        reset=Pin(23, Pin.OUT),
        cs=Pin(5, Pin.OUT),
        dc=Pin(16, Pin.OUT),
        backlight=backlight,
        rotation=rotation,
    )
    W = tft.physical_height        # 240 en horizontal (convención del slave)
    H = tft.physical_width         # 135

    backlight.value(0)             # arranca apagado

    btn_izq = Pin(config.PIN_BTN_LEFT,  Pin.IN, Pin.PULL_UP)
    btn_der = Pin(config.PIN_BTN_RIGHT, Pin.IN, Pin.PULL_UP)
    return tft


def backlight_on():
    if backlight:
        backlight.value(1)


def backlight_off():
    if backlight:
        backlight.value(0)


# ── helpers ──
def _cx(font, txt):
    return max(0, (W - tft.write_width(font, txt)) // 2)

def _derecha(font, txt, margen=4):
    return max(0, W - tft.write_width(font, txt) - margen)

def _fmt(v, suf=""):
    if v is None:
        return "--"
    if v == "ERR":
        return "ERR"
    return "{}{}".format(v, suf)

def _cat_aire(mq):
    if not isinstance(mq, (int, float)):
        return "--", GRIS
    if mq < 700:   return "BUENA", VERDE
    if mq < 1500:  return "MODERADA", AMARILLO
    if mq < 2500:  return "MALA", NARANJA
    return "MUY MALA", ROJO


def _header(node_id, hora, conectado):
    tft.write(sm, node_id, 4, 2, CYAN)
    if hora:
        tft.write(sm, hora, _derecha(sm, hora), 2, VERDE if conectado else GRIS)


def _barra(msg, col=VERDE):
    tft.fill_rect(0, 110, W, H - 110, NEGRO)
    tft.write(sm, msg[:30], 6, 114, col)


# ── PANTALLAS ──────────────────────────────────────────────
def boot(node_id):
    """Pantalla de arranque."""
    if not tft:
        return
    try:
        tft.fill(NEGRO)
        tft.write(md, "PIF NODE", _cx(md, "PIF NODE"), 22, VERDE)
        tft.write(sm, node_id, _cx(sm, node_id), 58, CYAN)
        tft.write(sm, "buscando master...", _cx(sm, "buscando master..."), 90, AMARILLO)
    except Exception as e:
        print("[UI] boot:", e)


def mostrar_normal(node_id, lec, hora="", conectado=False, vista=0):
    """Pantalla normal con vista rotativa: 0=ambiente, 1=aire, 2=movimiento."""
    if not tft:
        return
    try:
        tft.fill(NEGRO)
        _header(node_id, hora, conectado)

        if vista == 1:                      # ── AIRE ──
            mq = lec.get("mq")
            cat, cat_col = _cat_aire(mq)
            tft.write(sm, "CALIDAD DE AIRE", 4, 26, GRIS)
            tft.write(md, _fmt(mq), 4, 46, BLANCO if mq != "ERR" else ROJO)
            tft.write(md, cat, 4, 80, cat_col)

        elif vista == 2:                    # ── MOVIMIENTO ──
            ax = lec.get("ax"); ay = lec.get("ay"); az = lec.get("az")
            tft.write(sm, "MOVIMIENTO (g)", 4, 26, GRIS)
            tft.write(sm, "X: " + _fmt(ax), 4,   52, ROJO)
            tft.write(sm, "Y: " + _fmt(ay), 86,  52, VERDE)
            tft.write(sm, "Z: " + _fmt(az), 164, 52, CYAN)
            if ax is not None:
                mag = round((ax * ax + ay * ay + az * az) ** 0.5, 2)
                tft.write(sm, "|a| = {} g".format(mag), 4, 80, BLANCO)

        else:                               # ── AMBIENTE (default) ──
            t = lec.get("t"); h = lec.get("h")
            t_col = ROJO if t == "ERR" else AMARILLO
            h_col = ROJO if h == "ERR" else CYAN
            tft.write(md, "Temp: " + _fmt(t, " C"), 4, 28, t_col)
            tft.write(md, "Hum:  " + _fmt(h, " %"), 4, 64, h_col)

        if conectado:
            _barra("MALLA OK", VERDE)
        else:
            _barra("buscando master...", AMARILLO)
    except Exception as e:
        print("[UI] normal:", e)


def mostrar_alerta(node_id, nivel, sensores, lec, hora=""):
    """Pantalla de alerta (roja)."""
    if not tft:
        return
    try:
        col = ROJO if nivel == "CRIT" else NARANJA
        tft.fill(NEGRO)
        # marco
        tft.fill_rect(0, 0, W, 4, col)
        tft.fill_rect(0, H - 4, W, 4, col)
        tft.fill_rect(0, 0, 4, H, col)
        tft.fill_rect(W - 4, 0, 4, H, col)

        titulo = "! ALERTA {} !".format(nivel or "")
        tft.write(md, titulo, _cx(md, titulo), 16, col)
        tft.write(sm, node_id, _cx(sm, node_id), 48, CYAN)

        det = ", ".join(sensores) if sensores else "?"
        tft.write(sm, "Sensores: " + det, 8, 74, BLANCO)

        # un valor representativo
        if "Temp" in sensores:
            tft.write(sm, "Temp: " + _fmt(lec.get("t"), " C"), 8, 94, AMARILLO)
        elif "MQ135" in sensores:
            tft.write(sm, "Aire: " + _fmt(lec.get("mq")), 8, 94, AMARILLO)
        elif "Hum" in sensores:
            tft.write(sm, "Hum: " + _fmt(lec.get("h"), " %"), 8, 94, AMARILLO)
    except Exception as e:
        print("[UI] alerta:", e)


def mostrar_relay(node_de, estado="ENVIANDO..."):
    """Pantalla cuando este nodo retransmite un mensaje ajeno hacia el master."""
    if not tft:
        return
    try:
        col = VERDE if estado.strip().upper().startswith("ENVIADO") else AMARILLO
        tft.fill(NEGRO)
        tft.write(sm, "RELAY / MALLA", 4, 8, GRIS)
        tft.write(md, "Msg de:", 4, 32, BLANCO)
        tft.write(md, (node_de or "?")[:14], 4, 62, CYAN)
        tft.write(md, estado, _cx(md, estado), 98, col)
    except Exception as e:
        print("[UI] relay:", e)


def rotar(lec, hora="", conectado=False):
    """Compat: avanza una vista. main suele controlar 'vista' directamente."""
    mostrar_normal("", lec, hora, conectado, 0)
