# sens.py — sensores del slave PIF Mesh.
#
# Portado del sensores.py de Ana, SIN el MLX90614/GY-906 (sensor ya no
# usado). Quedan: DHT11 (Temp/Hum), MQ135 (aire) y MPU6050 (AccX/Y/Z),
# con autodetección por hardware y recuperación del bus I2C.
#
# leer() devuelve (med, lec):
#   med = [{"t":"Temp","v":..}, {"t":"Hum","v":..}, {"t":"AccX","v":..}, ...]
#   lec = {"t":..,"h":..,"mq":..,"ax":..,"ay":..,"az":..}  (resumen para UI)

from machine import Pin, I2C, ADC
import time, dht

ADDR_MPU = 0x68

# Rangos MQ135 (12 bits, 0-4095). MQ_MIN distingue un pin al aire (~470)
# de un MQ real conectado.
MQ_MIN, MQ_MAX, MQ_VARIANZA = 600, 3800, 300

# ── Alertas (antes alertas.py) — el sensor es dueño de sus umbrales ──
# t de la medición -> clave en UMBRALES
_MAP_UMBRAL = {"Temp": "Temp", "Hum": "Hum", "MQ135": "MQ135",
               "TempObj": "Temp_obj", "Temp_obj": "Temp_obj"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _eval_alto(prev, v, u):
    """Sensor que alerta cuando SUBE (Temp, MQ135). Histéresis warn/crit + *_sale."""
    if prev == "CRIT":
        if v < u["crit_sale"]:
            return "WARN" if v >= u["warn"] else "OK"
        return "CRIT"
    if prev == "WARN":
        if v >= u["crit"]:
            return "CRIT"
        if v <= u["warn_sale"]:
            return "OK"
        return "WARN"
    if v >= u["crit"]:
        return "CRIT"
    if v >= u["warn"]:
        return "WARN"
    return "OK"


def _eval_bajo(prev, v, u):
    """Sensor que alerta cuando BAJA (Hum). Histéresis bajo/bajo_crit + *_sale."""
    if prev == "CRIT":
        if v > u["bajo_crit_sale"]:
            return "WARN" if v <= u["bajo"] else "OK"
        return "CRIT"
    if prev == "WARN":
        if v <= u["bajo_crit"]:
            return "CRIT"
        if v >= u["bajo_sale"]:
            return "OK"
        return "WARN"
    if v <= u["bajo_crit"]:
        return "CRIT"
    if v <= u["bajo"]:
        return "WARN"
    return "OK"


# ===============================================
#  MPU-6050  (acelerómetro)
# ===============================================
class MPU6050:
    def __init__(self, i2c, address=ADDR_MPU):
        self.i2c = i2c
        self.address = address
        self.i2c.writeto_mem(address, 0x6B, b'\x00')   # despertar
        self.i2c.writeto_mem(address, 0x1B, b'\x00')   # gyro ±250
        self.i2c.writeto_mem(address, 0x1C, b'\x00')   # accel ±2g
        self.i2c.writeto_mem(address, 0x1A, b'\x06')   # filtro
        time.sleep_ms(100)

    @staticmethod
    def _i16(hi, lo):
        v = (hi << 8) | lo
        return v - 65536 if v >= 0x8000 else v

    def accel_g(self):
        raw = self.i2c.readfrom_mem(self.address, 0x3B, 6)
        return (round(self._i16(raw[0], raw[1]) / 16384.0, 2),
                round(self._i16(raw[2], raw[3]) / 16384.0, 2),
                round(self._i16(raw[4], raw[5]) / 16384.0, 2))


# ===============================================
#  RECUPERACIÓN DE BUS I2C  (por si el MPU cuelga el bus)
# ===============================================
def reset_bus_i2c(scl_pin=22, sda_pin=21, freq=100000):
    scl = Pin(scl_pin, Pin.OUT, value=1)
    sda = Pin(sda_pin, Pin.IN)
    if sda.value() == 1:
        I2C(0, scl=Pin(scl_pin), sda=Pin(sda_pin), freq=freq)
        return True
    for _ in range(9):
        scl.value(0); time.sleep_us(5)
        scl.value(1); time.sleep_us(5)
        if sda.value() == 1:
            break
    sda = Pin(sda_pin, Pin.OUT, value=0)
    time.sleep_us(5); scl.value(1)
    time.sleep_us(5); sda.value(1); time.sleep_us(5)
    I2C(0, scl=Pin(scl_pin), sda=Pin(sda_pin), freq=freq)
    time.sleep_ms(100)
    return Pin(sda_pin, Pin.IN).value() == 1


def escanear_con_recuperacion(intentos=3, scl=22, sda=21, freq=100000):
    for _ in range(intentos):
        i2c = I2C(0, scl=Pin(scl), sda=Pin(sda), freq=freq)
        time.sleep_ms(100)
        disp = i2c.scan()
        if disp:
            return i2c, disp
        reset_bus_i2c(scl, sda, freq)
        time.sleep_ms(500)
    return None, []


# ===============================================
#  PRESENCIA DEL MQ POR HARDWARE
#  Un pin al aire "sigue al pull" (0 con pull-down, 1 con pull-up).
#  Un MQ conectado y alimentado MANDA sobre el pin y no obedece al pull.
# ===============================================
def _mq_presente(pin_num):
    try:
        p = Pin(pin_num, Pin.IN, Pin.PULL_DOWN)
        time.sleep_ms(20)
        bajo = p.value()
        p = Pin(pin_num, Pin.IN, Pin.PULL_UP)
        time.sleep_ms(20)
        alto = p.value()
        flotante = (bajo == 0 and alto == 1)   # obedeció a ambos -> al aire
        return not flotante
    except Exception as e:
        print("[SENS] _mq_presente err:", e)
        return False


# ===============================================
#  CLASE PRINCIPAL — autodetección + lectura
# ===============================================
class Sensores:
    def __init__(self, pin_dht=15, pin_mq=33, scl=22, sda=21,
                 usar_dht="auto", usar_mq="auto", usar_mpu="auto",
                 umbrales=None):
        self.pin_dht, self.pin_mq = pin_dht, pin_mq
        self.scl, self.sda = scl, sda
        self.f_dht, self.f_mq, self.f_mpu = usar_dht, usar_mq, usar_mpu
        self.i2c = None
        self.mpu = self.dht = self.adc = None
        self.mq_estado = "ausente"      # ausente / sano / danado
        self.activos = []
        # estado de alertas (histéresis por sensor)
        self.umbrales = umbrales or {}
        self._nivel = {}                # {sensor: "OK"|"WARN"|"CRIT"}
        self._estado = "NORMAL"         # "NORMAL" | "ALERTA"

    def detectar(self):
        self.activos = []
        disp = []
        if self.f_mpu is not False:
            self.i2c, disp = escanear_con_recuperacion(scl=self.scl, sda=self.sda)
            print("[SENS] I2C:", [hex(d) for d in disp])

        # ── MPU6050 (I2C) ──
        if self.f_mpu is not False:
            presente = (ADDR_MPU in disp) or (self.f_mpu is True)
            if presente and self.i2c:
                try:
                    self.mpu = MPU6050(self.i2c); self.activos.append("MPU")
                except Exception as e:
                    print("[SENS] MPU err:", e); self.mpu = None

        # ── DHT11 (digital) ──
        if self.f_dht is not False:
            try:
                self.dht = dht.DHT11(Pin(self.pin_dht))
                if self.f_dht is True:
                    self.activos.append("DHT11")            # forzado
                else:
                    time.sleep(1); ok = False
                    for _ in range(3):
                        try:
                            self.dht.measure()
                            if not (self.dht.temperature() == 0 and self.dht.humidity() == 0):
                                ok = True; break
                        except OSError:
                            time.sleep(1)
                    if ok: self.activos.append("DHT11")
                    else:  self.dht = None
            except Exception as e:
                print("[SENS] DHT err:", e); self.dht = None

        # ── MQ135 (analógico) — presencia por HW + clasificación por rango ──
        self.mq_estado = "ausente"
        if self.f_mq is not False:
            try:
                presente = True if self.f_mq is True else _mq_presente(self.pin_mq)
                if not presente:
                    self.adc = None
                    print("[SENS] MQ: pin al aire (ausente)")
                else:
                    self.adc = ADC(Pin(self.pin_mq))
                    self.adc.atten(ADC.ATTN_11DB)
                    self.adc.width(ADC.WIDTH_12BIT)
                    if self.f_mq is True:
                        self.mq_estado = "sano"; self.activos.append("MQ135")
                    else:
                        vals = [self.adc.read() for _ in range(5)]
                        media = sum(vals) // len(vals)
                        var = max(vals) - min(vals)
                        if MQ_MIN <= media <= MQ_MAX and var <= MQ_VARIANZA:
                            self.mq_estado = "sano"
                        else:
                            self.mq_estado = "danado"      # conectado pero raro
                        self.activos.append("MQ135")
                        print("[SENS] MQ media:{} var:{} -> {}".format(media, var, self.mq_estado))
            except Exception as e:
                print("[SENS] MQ err:", e); self.adc = None

        print("[SENS] activos:", self.activos)
        return self.activos

    def leer(self):
        med, lec = [], {}

        if self.mpu:
            try:
                ax, ay, az = self.mpu.accel_g()
                lec["ax"], lec["ay"], lec["az"] = ax, ay, az
                med += [{"t": "AccX", "v": ax}, {"t": "AccY", "v": ay},
                        {"t": "AccZ", "v": az}]
            except Exception as e:
                print("[SENS] MPU leer:", e)

        if self.dht:
            t, h = "ERR", "ERR"
            for _ in range(3):
                try:
                    self.dht.measure()
                    tv, hv = self.dht.temperature(), self.dht.humidity()
                    if not (tv == 0 and hv == 0):
                        t, h = tv, hv; break
                except OSError:
                    time.sleep_ms(300)
            lec["t"], lec["h"] = t, h
            med += [{"t": "Temp", "v": t}, {"t": "Hum", "v": h}]

        if self.adc:
            if self.mq_estado == "danado":
                mq = "ERR"                       # presente pero fuera de rango
            else:
                try:
                    vals = [self.adc.read() for _ in range(5)]
                    mq = sum(vals) // len(vals)
                except Exception:
                    mq = "ERR"
            lec["mq"] = mq
            med.append({"t": "MQ135", "v": mq})

        return med, lec

    # ── ALERTAS (antes alertas.py) ─────────────────────────
    def set_umbrales(self, umbrales):
        self.umbrales = umbrales or {}
        self._nivel = {}
        self._estado = "NORMAL"

    def estado_actual(self):
        """Modo: 'NORMAL' o 'ALERTA' (el sueño profundo es mejora posterior)."""
        return self._estado

    def evaluar_alertas(self, med):
        """med = [{"t":..,"v":..}]. Devuelve (nivel_global, sensores_afectados).

        nivel_global: None | 'WARN' | 'CRIT'. Histéresis por sensor: ENTRA con
        un umbral y SALE con otro, para que la alerta no parpadee.
        """
        for m in med:
            clave = _MAP_UMBRAL.get(m.get("t"))
            if not clave or clave not in self.umbrales:
                continue
            v = _num(m.get("v"))
            if v is None:                       # "ERR"/no numérico -> conserva
                continue
            u = self.umbrales[clave]
            prev = self._nivel.get(m["t"], "OK")
            if "bajo" in u:
                self._nivel[m["t"]] = _eval_bajo(prev, v, u)
            else:
                self._nivel[m["t"]] = _eval_alto(prev, v, u)

        afectados = [t for t, s in self._nivel.items() if s in ("WARN", "CRIT")]
        if any(s == "CRIT" for s in self._nivel.values()):
            nivel = "CRIT"
        elif afectados:
            nivel = "WARN"
        else:
            nivel = None
        self._estado = "ALERTA" if nivel else "NORMAL"
        return nivel, afectados
