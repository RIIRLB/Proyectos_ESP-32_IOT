# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()

# boot.py — PIF Mesh LAB-ARTE
# Con el firmware LILYGO TTGO LoRa32 v1.22.2, el módulo espnow
# reserva 4 rx-buffers de radio al importarse en main.py.
# WiFi necesita 10 buffers y falla con 0x0101 si espnow va primero.
# Solución: activar WiFi aquí, antes de que main.py importe espnow.
import network
network.WLAN(network.STA_IF).active(True)