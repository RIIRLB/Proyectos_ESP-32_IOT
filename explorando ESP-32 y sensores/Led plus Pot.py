from machine import ADC, Pin, PWM

# Inicializa el ADC en el pin 4
adc = ADC(Pin(4))
adc.width(ADC.WIDTH_12BIT)
adc.atten(ADC.ATTN_11DB)

# Inicializa el PWM en el pin 2 para controlar el LED
led = PWM(Pin(2), freq=5000)

while True:
    # Lee el valor del ADC (entre 0 y 4095)
    valor = adc.read()
    
    # toma los datos del ADC a un rango de 0 a 1023 para el PWM
    duty = 1023 - int((valor / 4095) * 1023)
        ###usando logica invertida 
    # Ajusta el ciclo de trabajo del PWM para controlar el brillo del LED
    led.duty(duty)
    print("Valor del potenciómetro:", valor, "Ciclo de trabajo del LED:", duty)