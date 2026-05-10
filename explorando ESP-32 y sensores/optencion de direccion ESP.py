import network
from oled import Display

# Para que se pueda imprimir los mensajes en el dislpay
display = Display()

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
print(wlan.config('mac'))

display.text("ESP 3 lista para usarse el MAC es: {}". format (wlan.config('mac')))