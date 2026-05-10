class Print:
    def __init__(self, tft, font=None, fg=0xFFFF, bg=0x0000, x=0, y=0, line_height=10):
        self.tft = tft
        self.font = font
        self.fg = fg
        self.bg = bg
        self.x = x
        self.y = y
        self.line_height = line_height
        self.cursor_x = x
        self.cursor_y = y

    def __call__(self, text):
        """Permite usar print("texto") para escribir en pantalla."""
        if isinstance(text, (int, float)):
            text = str(text)

        # Si se sale de pantalla, limpia y reinicia
        if self.cursor_y + self.line_height > self.tft.height:
            self.tft.fill(self.bg)
            self.cursor_y = self.y

        self.tft.text(
            self.font,
            text,
            self.cursor_x,
            self.cursor_y,
            self.fg,
            self.bg
        )
        self.cursor_y += self.line_height

    def reset(self):
        """Limpia pantalla y reinicia cursor."""
        self.tft.fill(self.bg)
        self.cursor_x = self.x
        self.cursor_y = self.y
