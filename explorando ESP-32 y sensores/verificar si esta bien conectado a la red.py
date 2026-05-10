import network
import time

sta = network.WLAN(network.STA_IF)
sta.active(True)

print("Activando WiFi...")

if not sta.active():
    print("WiFi no se activó")
else:
    print("WiFi activado")

print("Intentando conectar...")
sta.connect("Totalplay-C5AC", "C5AC642BDVePRn6Z")

for i in range(20):
    if sta.isconnected():
        print("Conectado:", sta.ifconfig())
        break
    print(".", end="")
    time.sleep(0.5)
else:
    print("\n No se pudo conectar.")
