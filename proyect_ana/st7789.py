# st7789.py

from machine import Pin
from math import sin, cos
import struct, framebuf, time

# Comandos SPI
_CASET  = b"\x2a"
_RASET  = b"\x2b"
_RAMWR  = b"\x2c"
_BUFFER_SIZE = 256

# Conversión de color
def color565(r, g=0, b=0):
    if isinstance(r, (tuple, list)):
        r, g, b = r[:3]
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return ((c & 0xFF) << 8) | (c >> 8)

# Colores predefinidos
BLACK   = color565(0,   0,   0)
WHITE   = color565(255, 255, 255)
RED     = color565(255, 0,   0)
GREEN   = color565(0,   255, 0)
BLUE    = color565(0,   0,   255)
CYAN    = color565(0,   255, 255)
MAGENTA = color565(255, 0,   255)
YELLOW  = color565(255, 255, 0)
ORANGE  = color565(255, 128, 0)

# Tablas de rotación
# Formato: (madctl, width, height, xstart, ystart)
_ROT_135x240 = (
    (0x00, 135, 240, 52, 40),
    (0x60, 240, 135, 40, 53),
    (0xC0, 135, 240, 53, 40),
    (0xA0, 240, 135, 40, 52),
)
_ROT_240x320 = (
    (0x00, 240, 320, 0, 0),
    (0x60, 320, 240, 0, 0),
    (0xC0, 240, 320, 0, 0),
    (0xA0, 320, 240, 0, 0),
)
_ROT_240x240 = (
    (0x00, 240, 240,  0,  0),
    (0x60, 240, 240,  0,  0),
    (0xC0, 240, 240,  0, 80),
    (0xA0, 240, 240, 80,  0),
)
_SUPPORTED = {
    (135, 240): _ROT_135x240,
    (240, 320): _ROT_240x320,
    (240, 240): _ROT_240x240,
}


class Display:

    def __init__(self, spi, dc, cs=None, rst=None, bl=None,
                 width=135, height=240, rotation=0, invert=True,
                 # aliases para compatibilidad con tft_config
                 reset=None, backlight=None):

        # Aceptar alias de parámetros
        if reset    is not None: rst = reset
        if backlight is not None: bl = backlight

        key = (min(width, height), max(width, height))
        phys_key = (135, 240) if key == (135, 240) else \
                   (240, 320) if key == (240, 320) else \
                   (240, 240) if key == (240, 240) else None
        if phys_key is None or phys_key not in _SUPPORTED:
            raise ValueError("Display {}x{} no soportado".format(width, height))

        self._rotations = _SUPPORTED[phys_key]

        # Dimensiones físicas panel
        self.physical_width  = width
        self.physical_height = height

        self.spi     = spi
        self.dc      = dc
        self.cs      = cs
        self.rst     = rst
        self.bl      = bl
        self._invert = invert

        self._init_display()
        self._buf = None
        self.rotation(rotation)
        self.fb.fill(BLACK)
        self.fill(BLACK)
        if bl:
            bl.on() if hasattr(bl, 'on') else bl.value(1)

        # Cursor interno para print()
        self._cx = 0
        self._cy = 0
        self._cx_origin = 0

    # Escritura SPI
    def _write(self, cmd=None, data=None):
        if self.cs: self.cs.off()
        if cmd is not None:
            self.dc.off()
            self.spi.write(cmd if isinstance(cmd, (bytes, bytearray)) else bytes([cmd]))
        if data is not None:
            self.dc.on()
            self.spi.write(data if isinstance(data, (bytes, bytearray)) else bytes([data]))
        if self.cs: self.cs.on()

    # Inicialización del panel
    def _init_display(self):
        if self.bl:
            self.bl.off() if hasattr(self.bl, 'off') else self.bl.value(0)
        if self.rst:
            self.rst.off(); time.sleep_ms(100)
            self.rst.on();  time.sleep_ms(200)
        self._write(0x01); time.sleep_ms(150)   # SW reset
        self._write(0x11); time.sleep_ms(150)   # Sleep out
        self._write(0x3A, b'\x55'); time.sleep_ms(10)  # 16-bit color
        self._write(0xB2, b'\x0C\x0C\x00\x33\x33')
        self._write(0xB7, b'\x35')
        self._write(0xBB, b'\x19')
        self._write(0xC0, b'\x2C')
        self._write(0xC2, b'\x01')
        self._write(0xC3, b'\x12')
        self._write(0xC4, b'\x20')
        self._write(0xC6, b'\x0F')
        self._write(0xD0, b'\xA4\xA1')
        self._write(0xE0, b'\xD0\x04\x0D\x11\x13\x2B\x3F\x54\x4C\x18\x0D\x0B\x1F\x23')
        self._write(0xE1, b'\xD0\x04\x0C\x11\x13\x2C\x3F\x44\x51\x2F\x1F\x1F\x20\x23')
        self._write(0x21 if self._invert else 0x20)
        self._write(0x29); time.sleep_ms(50)    # Display on
        if self.bl:
            self.bl.on() if hasattr(self.bl, 'on') else self.bl.value(1)

    # Rotación
    def rotation(self, r):
        r = r % 4
        madctl, self.width, self.height, self.xstart, self.ystart = self._rotations[r]
        self._write(0x36, bytes([madctl]))
        self._buf = bytearray(self.width * self.height * 2)
        self.fb   = framebuf.FrameBuffer(self._buf, self.width, self.height, framebuf.RGB565)

    # Ventana de píxeles
    def _set_window(self, x0, y0, x1, y1):
        xs = x0 + self.xstart; xe = x1 + self.xstart
        ys = y0 + self.ystart; ye = y1 + self.ystart
        self._write(_CASET, struct.pack(">HH", xs, xe))
        self._write(_RASET, struct.pack(">HH", ys, ye))
        self._write(_RAMWR)

    # Volcar framebuffer a pantalla
    def show(self):
        self._set_window(0, 0, self.width - 1, self.height - 1)
        if self.cs: self.cs.off()
        self.dc.on()
        self.spi.write(self._buf)
        if self.cs: self.cs.on()

    # Rellenos
    def fill(self, color):
        """Rellena toda la pantalla directamente (sin framebuffer)."""
        self._set_window(0, 0, self.width - 1, self.height - 1)
        pixel = struct.pack(">H", color)
        chunk, rest = divmod(self.width * self.height, _BUFFER_SIZE)
        data = pixel * _BUFFER_SIZE
        if self.cs: self.cs.off()
        self.dc.on()
        for _ in range(chunk): self.spi.write(data)
        if self.cs: self.cs.on()
        if rest: self._write(None, pixel * rest)

    def fill_rect(self, x, y, w, h, color):
        if w <= 0 or h <= 0: return
        self._set_window(x, y, x + w - 1, y + h - 1)
        pixel = struct.pack(">H", color)
        chunk, rest = divmod(w * h, _BUFFER_SIZE)
        data = pixel * _BUFFER_SIZE
        if chunk:
            if self.cs: self.cs.off()
            self.dc.on()
            for _ in range(chunk): self.spi.write(data)
            if self.cs: self.cs.on()
        if rest: self._write(None, pixel * rest)

    # Primitivas
    def hline(self, x, y, l, c): self.fill_rect(x, y, l, 1, c)
    def vline(self, x, y, l, c): self.fill_rect(x, y, 1, l, c)

    def rect(self, x, y, w, h, color):
        self.hline(x, y,     w, color)
        self.hline(x, y+h-1, w, color)
        self.vline(x,     y, h, color)
        self.vline(x+w-1, y, h, color)

    def pixel(self, x, y, color):
        self._set_window(x, y, x, y)
        self._write(None, struct.pack(">H", color))

    def blit_buffer(self, buf, x, y, w, h):
        self._set_window(x, y, x + w - 1, y + h - 1)
        self._write(None, buf)

    # Tu API de texto (framebuffer)
    
    def draw_text(self, txt, x, y, color,
                  scale=1, wrap=False, align="left",
                  max_w=None, line_h=None, bg=BLACK):

        CH_W = 8 * scale
        CH_H = 8 * scale
        if max_w  is None: max_w  = self.width - x
        if line_h is None: line_h = CH_H + 2

        max_chars = max(1, max_w // CH_W)
        lineas = []
        for seg in txt.split('\n'):
            if not wrap:
                lineas.append(seg)
            else:
                palabras = seg.split(' ')
                linea = ''
                for p in palabras:
                    prueba = linea + (' ' if linea else '') + p
                    if len(prueba) <= max_chars:
                        linea = prueba
                    else:
                        if linea: lineas.append(linea)
                        while len(p) > max_chars:
                            lineas.append(p[:max_chars])
                            p = p[max_chars:]
                        linea = p
                if linea: lineas.append(linea)

        cur_y = y
        for linea in lineas:
            if align == "center":
                px = x + max(0, (max_w - len(linea) * CH_W) // 2)
            elif align == "right":
                px = x + max(0, max_w - len(linea) * CH_W)
            else:
                px = x

            if scale == 1:
                self.fb.text(linea[:max_chars], px, cur_y, color)
            else:
                tiny_buf = bytearray(128)
                tiny_fb  = framebuf.FrameBuffer(tiny_buf, 8, 8, framebuf.RGB565)
                cur_x = px
                for ch in linea:
                    if cur_x + 8 * scale > px + max_w or cur_x + 8 * scale > self.width:
                        break
                    tiny_fb.fill(bg)
                    tiny_fb.text(ch, 0, 0, color)
                    for row in range(8):
                        for col in range(8):
                            i   = (row * 8 + col) * 2
                            px2 = (tiny_buf[i] << 8) | tiny_buf[i + 1]
                            for sy in range(scale):
                                dy = cur_y + row * scale + sy
                                if dy >= self.height: continue
                                for sx in range(scale):
                                    dx = cur_x + col * scale + sx
                                    if dx < self.width:
                                        self.fb.pixel(dx, dy, px2)
                    cur_x += 8 * scale
            cur_y += line_h
        return cur_y

    def cursor(self, x=None, y=None, reset=False):

        if reset:
            self._cx = self._cy = self._cx_origin = 0
            return (0, 0)
        if x is not None: self._cx = self._cx_origin = x
        if y is not None: self._cy = y
        return (self._cx, self._cy)

    def print(self, *args, x=None, y=None, scale=1,
              wrap=False, align="left", max_w=None, line_h=None, end="\n"):

        CH_H = 8 * scale
        if line_h is None: line_h = CH_H + 2
        if x is not None: self._cx = self._cx_origin = x
        if y is not None: self._cy = y
        if max_w is None: max_w = self.width - self._cx


        segs = []
        if len(args) == 2 and isinstance(args[0], str) and not isinstance(args[1], tuple):
            segs = [(args[0], args[1])]
        else:
            for a in args:
                if isinstance(a, tuple) and len(a) == 2:
                    segs.append(a)
                elif isinstance(a, str):
                    segs.append((a, WHITE))

        if not segs: return

        cur_x = self._cx
        max_y = self._cy
        for txt, color in segs:
            seg_max_w = self.width - cur_x
            fin_y = self.draw_text(txt, cur_x, self._cy, color,
                                   scale=scale, wrap=wrap, align=align,
                                   max_w=seg_max_w, line_h=line_h)
            cur_x += len(txt.split('\n')[-1]) * 8 * scale
            if cur_x > self.width: cur_x = self._cx_origin
            if fin_y > max_y: max_y = fin_y

        if end == "\n":
            self._cy = max_y
            self._cx = self._cx_origin
        else:
            self._cx = cur_x

    def header(self, segmentos, y=0, split=None, line_h=None):

        n = len(segmentos)
        if n == 2:
            s = split if isinstance(split, int) else self.width // 2
            zonas = [(0, s), (s, self.width - s)]
        elif n == 3:
            if isinstance(split, (tuple, list)):
                s0, s1 = split
            else:
                s0 = self.width // 3
                s1 = (self.width * 2) // 3
            zonas = [(0, s0), (s0, s1 - s0), (s1, self.width - s1)]
        else:
            raise ValueError("header() acepta 2 o 3 segmentos")

        max_y = y
        for seg, (x_ini, zona_w) in zip(segmentos, zonas):
            fin_y = self.draw_text(
                seg.get("txt", ""), x_ini, y,
                seg.get("color", WHITE),
                scale=seg.get("scale", 1),
                wrap=False,
                align=seg.get("align", "left"),
                max_w=zona_w, line_h=line_h
            )
            if fin_y > max_y: max_y = fin_y
        return max_y

    # API de Comfortaa

    def write(self, font, text, x, y, color=WHITE, bg=BLACK):

        fg = ((color << 8) & 0xFF00) | (color >> 8)
        bg_sw = ((bg << 8) & 0xFF00) | (bg >> 8)

        buf_len  = font.HEIGHT * font.MAX_WIDTH * 2
        buf      = bytearray(buf_len)
        fg_hi    = fg >> 8;   fg_lo = fg & 0xFF
        bg_hi    = bg_sw >> 8; bg_lo = bg_sw & 0xFF

        for character in text:
            try:
                char_index   = font.MAP.index(character)
                offset       = char_index * font.OFFSET_WIDTH
                bs_bit       = font.OFFSETS[offset]
                if font.OFFSET_WIDTH > 1:
                    bs_bit = (bs_bit << 8) + font.OFFSETS[offset + 1]
                if font.OFFSET_WIDTH > 2:
                    bs_bit = (bs_bit << 8) + font.OFFSETS[offset + 2]

                char_width   = font.WIDTHS[char_index]
                buf_needed   = char_width * font.HEIGHT * 2

                for i in range(0, buf_needed, 2):
                    if font.BITMAPS[bs_bit // 8] & (1 << (7 - (bs_bit % 8))):
                        buf[i] = fg_hi; buf[i + 1] = fg_lo
                    else:
                        buf[i] = bg_hi; buf[i + 1] = bg_lo
                    bs_bit += 1

                to_col = x + char_width - 1
                to_row = y + font.HEIGHT - 1
                if self.width > to_col and self.height > to_row:
                    self._set_window(x, y, to_col, to_row)
                    self._write(None, buf[:buf_needed])
                x += char_width

            except ValueError:
                pass  

    def write_width(self, font, text):

        width = 0
        for character in text:
            try:
                char_index = font.MAP.index(character)
                width += font.WIDTHS[char_index]
            except ValueError:
                pass
        return width

    # Línea diagonal
    def line(self, x0, y0, x1, y1, color):

        steep = abs(y1 - y0) > abs(x1 - x0)
        if steep:
            x0, y0 = y0, x0
            x1, y1 = y1, x1
        if x0 > x1:
            x0, x1 = x1, x0
            y0, y1 = y1, y0
        dx  = x1 - x0
        dy  = abs(y1 - y0)
        err = dx // 2
        ystep = 1 if y0 < y1 else -1
        while x0 <= x1:
            self.pixel(y0, x0, color) if steep else self.pixel(x0, y0, color)
            err -= dy
            if err < 0:
                y0  += ystep
                err += dx
            x0 += 1

    # Polígono
    def polygon(self, points, x, y, color, angle=0, center_x=0, center_y=0):

        if len(points) < 3:
            raise ValueError("polygon() necesita al menos 3 puntos")
        if angle:
            ca = cos(angle); sa = sin(angle)
            rotated = [
                (
                    x + center_x + int((p[0]-center_x)*ca - (p[1]-center_y)*sa),
                    y + center_y + int((p[0]-center_x)*sa + (p[1]-center_y)*ca),
                )
                for p in points
            ]
        else:
            rotated = [(x + int(p[0]), y + int(p[1])) for p in points]

        for i in range(1, len(rotated)):
            self.line(rotated[i-1][0], rotated[i-1][1],
                      rotated[i][0],   rotated[i][1], color)
        # Cerrar el polígono
        self.line(rotated[-1][0], rotated[-1][1],
                  rotated[0][0],  rotated[0][1], color)

    # Bitmaps (imágenes convertidas a módulo Python)
    def bitmap(self, bitmap, x, y, index=0):

        w  = bitmap.WIDTH
        h  = bitmap.HEIGHT
        to_col = x + w - 1
        to_row = y + h - 1
        if self.width <= to_col or self.height <= to_row:
            return

        bpp      = bitmap.BPP
        bs_bit   = bpp * w * h * index
        palette  = bitmap.PALETTE
        buf      = bytearray(w * h * 2)

        for i in range(0, len(buf), 2):
            color_index = 0
            for _ in range(bpp):
                color_index = (color_index << 1) | (
                    (bitmap.BITMAP[bs_bit >> 3] >> (7 - (bs_bit & 7))) & 1
                )
                bs_bit += 1
            color = palette[color_index]
            buf[i]   = color >> 8
            buf[i+1] = color & 0xFF

        self._set_window(x, y, to_col, to_row)
        self._write(None, buf)

    def pbitmap(self, bitmap, x, y, index=0):

        w      = bitmap.WIDTH
        h      = bitmap.HEIGHT
        bpp    = bitmap.BPP
        bs_bit = bpp * w * h * index
        palette = bitmap.PALETTE
        buf    = bytearray(w * 2)

        for row in range(h):
            for col in range(w):
                color_index = 0
                for _ in range(bpp):
                    color_index = (color_index << 1) | (
                        (bitmap.BITMAP[bs_bit >> 3] >> (7 - (bs_bit & 7))) & 1
                    )
                    bs_bit += 1
                color = palette[color_index]
                buf[col*2]   = color >> 8
                buf[col*2+1] = color & 0xFF

            to_col = x + w - 1
            to_row = y + row
            if self.width > to_col and self.height > to_row:
                self._set_window(x, y + row, to_col, to_row)
                self._write(None, buf)

    # Control de pantalla
    def invert(self, on=True):
        self._write(0x21 if on else 0x20)

    def sleep(self, on=True):
        self._write(0x10 if on else 0x11)
        time.sleep_ms(120)

    def backlight(self, on=True):
        if self.bl:
            if hasattr(self.bl, 'on'):
                self.bl.on() if on else self.bl.off()
            else:
                self.bl.value(1 if on else 0)
