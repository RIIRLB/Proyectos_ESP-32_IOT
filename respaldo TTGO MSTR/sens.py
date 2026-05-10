# sens_v3, DHT11, GY906, MQ135
from machine import Pin, PWM, I2C, ADC
import dht
import time
import mlx90614
import st7789py as st7789
import comfortaa_24 as font

class Sensores:
    def __init__(self, tft, p_servo=None, p_dht=None, p_mq135=None, p_btn_dir=None, p_btn_env=None):
        self.tft = tft
        
        # --- CORRECCIÓN DE PINES (Uso de "is not None") ---
        self.btn_dir = Pin(p_btn_dir, Pin.IN, Pin.PULL_UP) if p_btn_dir is not None else None
        self.btn_env = Pin(p_btn_env, Pin.IN, Pin.PULL_UP) if p_btn_env is not None else None
        self.servo = PWM(Pin(p_servo), freq=50) if p_servo is not None else None
        
        # --- SENSORES ---
        try:
            self.dht = dht.DHT11(Pin(p_dht)) if p_dht is not None else None
        except: self.dht = None
        
        try:
            self.i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50000)
            self.termometro = mlx90614.MLX90614(self.i2c)
        except: self.termometro = None

        if p_mq135 is not None:
            self.gas = ADC(Pin(p_mq135))
            self.gas.atten(ADC.ATTN_11DB)
            self.gas.width(ADC.WIDTH_12BIT)
        else: self.gas = None

    # --- LECTURAS ---
    def leer_dht(self):
        try:
            self.dht.measure()
            return self.dht.temperature(), self.dht.humidity()
        except: return "Error", "Error"

    def leer_termometro(self):
        try: return self.termometro.read_ambient_temp(), self.termometro.read_object_temp()
        except: return "Err", "Err"

    def leer_gas(self):
        if not self.gas: return 0, "N/C"
        val = self.gas.read()
        if val <= 5 or val >= 4090: return val, "ERROR"
        estado = "CONTAM" if val >= 1500 else "NORMAL"
        return val, estado

    # --- FORMATO DE PANTALLA (Solicitado) ---
    def mostrar_en_pantalla(self, tipo, status="SISTEMA OK"):
        self.tft.fill(st7789.BLACK)
        self.tft.write(font, "MODO: " + tipo, 10, 5, st7789.CYAN)
        
        if tipo == "DHT11":
            t, h = self.leer_dht()
            self.tft.write(font, "Temp: {}C".format(t), 10, 50, st7789.WHITE)
            self.tft.write(font, "Hum:  {}%".format(h), 10, 85, st7789.WHITE)
            
        elif tipo == "GY906": # Sensor de Ana
            amb, obj = self.leer_termometro()
            self.tft.write(font, "Amb: {:.1f}C".format(amb) if isinstance(amb, float) else "Err", 10, 50, st7789.WHITE)
            color = st7789.RED if (isinstance(obj, float) and obj >= 38) else st7789.GREEN
            self.tft.write(font, "Obj: {:.1f}C".format(obj) if isinstance(obj, float) else "Err", 10, 85, color)
            if isinstance(obj, float): # Barra de Ana
                self.tft.rect(210, 20, 20, 90, st7789.WHITE)
                relleno = int(((max(27, min(obj, 42)) - 27) / 15) * 88)
                self.tft.fill_rect(211, 109 - relleno, 18, relleno, color)
            
        elif tipo == "MQ135": # Sensor de Diego
            val, aire = self.leer_gas()
            self.tft.write(font, "Gas: {}".format(val), 10, 50, st7789.WHITE)
            color_a = st7789.GREEN if aire == "NORMAL" else st7789.RED
            self.tft.write(font, "Aire: " + aire, 10, 85, color_a)

        self.tft.write(font, status, 10, 115, st7789.YELLOW)