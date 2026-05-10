# Desplegando texto en Display de forma ordenada como CLASE
# RILB
# Ahora reconoce paralras grandes y las pasa a la siguiente linea
# Ahora con solo poner \n puedes hacer saltos de linea
from machine import Pin, I2C
from ssd1306 import SSD1306_I2C

class Display:
    def __init__(self, width=128, height=64, scl_pin=22, sda_pin=21):
        self.width = width
        self.height = height
        self.i2c = I2C(0, scl=Pin(scl_pin), sda=Pin(sda_pin))
        self.display = SSD1306_I2C(width, height, self.i2c)
        self.line_height = 10
        self.chars_per_line = self.width // 8
        self.max_lines = self.height // self.line_height

    def clear(self):
        self.display.fill(0)
        self.display.show()

    def show(self):
        self.display.show()

    def text(self, message, x=0, y=0):
        self.display.fill(0)
        lines = []
        for raw_line in message.split('\n'):
            words = raw_line.split()
            current_line = ""
            for word in words:
                if len(current_line + " " + word) <= self.chars_per_line:
                    if current_line:
                        current_line += " " + word
                    else:
                        current_line = word
                else:
                    lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)

        for i, line in enumerate(lines[:self.max_lines]):
            self.display.text(line, x, y + i * self.line_height)

        self.display.show()
