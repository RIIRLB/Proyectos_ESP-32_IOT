from machine import Pin,Timer,PWM

senal = Pin(25, Pin.OUT)
sensor = Pin(15, Pin.IN, Pin.PULL_UP)

contador = 0
cuenta_inicial = None
cuenta_final = None

def actualiza_contador(timer):
    global contador
    contador += 1

timer = Timer(0)
timer.init(period=1, mode=Timer.PERIODIC, callback=actualiza_contador)

def isr(pin):
    global contador, cuenta_inicial, cuenta_final
    if cuenta_inicial is None:
        cuenta_inicial = contador
    elif cuenta_final is None:
        cuenta_final = contador
    
    
sensor.irq(isr, trigger=Pin.IRQ_RISING)
pwm = PWM(senal, freq=10, duty_u16=65535-65535//2)

while cuenta_inicial is None  or cuenta_final is None:
    pass

print(f'el periodo es:{cuenta_final-cuenta_inicial}')
timer.deinit()