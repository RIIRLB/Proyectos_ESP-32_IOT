# display_manager.py
import sys

class DisplayManager:
    def __init__(self):
        self.is_tft = False

        # Intentar cargar TFT (TTGO T-Display)
        try:
            import tft_config
            import st7789py as st7789
            import comfortaa_24 as font

            self.st7789 = st7789
            self.font = font
            self.tft = tft_config.config(rotation=1)
            self.is_tft = True

            # Color por defecto
            self.color_fg = st7789.WHITE
            self.color_bg = st7789.BLACK

        except Exception as e:
            # Si falla, usar OLED
            from oled import Display
            self.oled = Display()
            self.is_tft = False

    # -----------------------------------------------------
    # MÉTODO PÚBLICO: escribir texto en OLED o TTGO
    # -----------------------------------------------------
    def write(self, text):
        if self.is_tft:
            self._write_tft(text)
        else:
            self._write_oled(text)
            
    # -----------------------------------------------------
    # OLED 128x64
    # -----------------------------------------------------
    def _write_oled(self, text):
        self.oled.clear()

        w = 128
        h = 64
        char_w = 8
        char_h = 8

        lines = text.split("\n")
        total_h = len(lines) * char_h
        y = (h - total_h) // 2

        for line in lines:
            text_w = len(line) * char_w
            x = (w - text_w) // 2
            self.oled.text(line, x, y)
            y += char_h

        self.oled.show()

    # -----------------------------------------------------
    # TTGO ST7789 TFT 135x240
    # -----------------------------------------------------

    # Ajusta el ancho del texto 
    def wrap_text(self, text, max_chars):
        words = text.split(" ")
        lines = []
        current = ""

        for w in words:
            if len(current) + len(w) + 1 <= max_chars:
                current += (" " if current else "") + w
            else:
                lines.append(current)
                current = w
        if current:
            lines.append(current)

        return lines
    
    # Centra el texto
    def draw_lines_centered(self, lines):
        W = self.tft.width
        H = self.tft.height
        char_h = self.font.HEIGHT
        char_w = self.font.MAX_WIDTH

        total_h = len(lines) * char_h
        y = (H - total_h) // 2

        for line in lines:
            text_w = len(line) * char_w
            x = (W - text_w) // 2

            self.tft.write(
                self.font,
                line,
                x,
                y,
                self.color_fg,
                self.color_bg
            )
            y += char_h
            
    # Para cambiar los colores del texto cuando se requiera
    # ej. screen.write("Hola [red]RIC[] necesitamos su precencia")
    def color_segments(self, text):
        segments = []
        current = ""
        color = self.color_fg

        i = 0
        while i < len(text):
            if text[i] == '[':
                # agregar segmento actual
                if current:
                    segments.append((current, color))
                    current = ""

                # leer color o cierre
                j = text.find(']', i)
                tag = text[i+1:j]

                if tag == "":
                    color = self.color_fg   # vuelve al color original
                else:
                    color = getattr(st7789, tag.upper(), self.color_fg)

                i = j + 1
            else:
                current += text[i]
                i += 1

        if current:
            segments.append((current, color))

        return segments
    
    # ahora si imprime bien el texto
    def _write_tft(self, text):
        self.tft.fill(self.color_bg)

        W = self.tft.width
        H = self.tft.height
        char_w = self.font.MAX_WIDTH
        char_h = self.font.HEIGHT

        max_chars = W // char_w

        # 1. Colores
        segments = self.color_segments(text)

        # 2. Reconstruir texto plano para el wrapping
        flat_text = "".join(seg[0] for seg in segments)

        # 3. Wrapping
        wrapped = self.wrap_text(flat_text, max_chars)

        # 4. Cálculo de centrado vertical
        total_h = len(wrapped) * char_h
        y = (H - total_h) // 2

        # 5. Imprimir cada línea segmentada
        seg_index = 0
        seg_text, seg_color = segments[seg_index]
        seg_pos = 0

        for line in wrapped:
            text_w = len(line) * char_w
            x = (W - text_w) // 2
            cx = x

            for ch in line:

                # Avanzar segmento si se agotó
                while seg_pos >= len(seg_text):
                    seg_index += 1
                    if seg_index < len(segments):
                        seg_text, seg_color = segments[seg_index]
                        seg_pos = 0
                    else:
                        seg_text = ""
                        seg_color = self.color_fg
                        break

                # Imprimir caracter
                self.tft.write(
                    self.font,
                    ch,
                    cx,
                    y,
                    seg_color,
                    self.color_bg
                )

                cx += char_w
                seg_pos += 1

            y += char_h


