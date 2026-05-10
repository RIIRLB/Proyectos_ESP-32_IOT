import gc, network, espnow, machine, utime, json, esp32
from machine import Pin
from umqtt.simple import MQTTClient

import tft_config
import st7789py as st7789
import comfortaa_16 as font_sm
import comfortaa_24 as font_md
from sens import Sensores

gc.collect()

# ───────── CONFIG ─────────
WIFI_SSID   = "Arte_Tenda5"
WIFI_PASS   = "Lab4rt3#"
MQTT_BROKER = "192.168.1.146"

CLIENT_ID   = "MASTER_TTGO"
TOPIC_SUB   = b"comandos/mesh"
TOPIC_PUB   = b"datos/sensores"

BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

SLEEP_MS      = 15000
VENTANA_MQTT  = 3000
VENTANA_MALLA = 3000

cola_subida = []
cola_bajada = []

# ───────── HARDWARE ─────────
tft = tft_config.config(rotation=1)
backlight = Pin(4, Pin.OUT)
backlight.value(1)

hw = Sensores(tft=tft, p_dht=15, p_mq135=34, p_btn_dir=0, p_btn_env=35)

esp32.wake_on_ext0(pin=Pin(0), level=esp32.WAKEUP_ALL_LOW)

def ui(msg, sub="", color=st7789.WHITE):
    tft.fill(st7789.BLACK)
    tft.write(font_md, "MASTER", 10, 10, st7789.GREEN)
    tft.write(font_sm, msg, 10, 60, color)
    if sub:
        tft.write(font_sm, sub, 10, 90, st7789.YELLOW)

# ───────── WIFI + ESP-NOW (UNA SOLA VEZ) ─────────
sta = network.WLAN(network.STA_IF)
sta.active(True)

if not sta.isconnected():
    sta.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(20):
        if sta.isconnected():
            break
        utime.sleep_ms(500)

print("WiFi conectado:", sta.isconnected())
print("Canal WiFi:", sta.config('channel'))

# ESP-NOW permanente
en = espnow.ESPNow()
en.active(True)
en.add_peer(BROADCAST_MAC)

# ───────── MQTT ─────────
def mqtt_callback(topic, msg):
    print("[MQTT RX]", msg)
    paquete = json.dumps({
        "type": "CMD",
        "cmd": msg.decode(),
        "origin": "MASTER"
    })
    cola_bajada.append(paquete)

def ciclo_mqtt():
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=30)
        client.set_callback(mqtt_callback)
        client.connect()
        client.subscribe(TOPIC_SUB)

        ui("MQTT", "Escuchando...", st7789.CYAN)

        t0 = utime.ticks_ms()
        while utime.ticks_diff(utime.ticks_ms(), t0) < VENTANA_MQTT:
            client.check_msg()
            utime.sleep_ms(50)

        # Enviar datos a Raspberry
        while cola_subida:
            msg = cola_subida.pop(0)
            client.publish(TOPIC_PUB, msg)
            print("[MQTT TX]", msg)

        client.disconnect()

    except Exception as e:
        print("MQTT ERROR:", e)

# ───────── ESP-NOW ─────────
def ciclo_malla():

    #  enviar comandos
    while cola_bajada:
        cmd = cola_bajada.pop(0)
        for _ in range(2):
            en.send(BROADCAST_MAC, cmd)
            utime.sleep_ms(100)
        print("[ESP TX]", cmd)

    # recibir datos
    ui("MALLA", "Escuchando...", st7789.BLUE)

    t0 = utime.ticks_ms()
    while utime.ticks_diff(utime.ticks_ms(), t0) < VENTANA_MALLA:

        res = en.recv(10)
        if res:
            host, msg = res
            if msg:
                try:
                    data = msg.decode()
                    cola_subida.append(data)
                    print("[ESP RX]", data)
                    ui("RX OK", data[:20], st7789.GREEN)
                    utime.sleep_ms(300)
                except:
                    pass

# ───────── LOOP ─────────
while True:
    try:
        backlight.value(1)

        # 🌡 Medición
        t, h = hw.leer_dht()
        hora = "{:02d}:{:02d}".format(*utime.localtime()[3:5])
        payload = f"{hora},{CLIENT_ID},Clima,{t}T-{h}H"
        cola_subida.append(payload)

        # ☁️ MQTT
        ciclo_mqtt()

        # 📡 ESP-NOW
        ciclo_malla()

        # 😴 Sleep (SIN apagar WiFi)
        ui("SLEEP", f"{SLEEP_MS//1000}s", st7789.WHITE)
        utime.sleep_ms(1000)

        backlight.value(0)
        machine.lightsleep(SLEEP_MS)

    except Exception as e:
        print("ERROR LOOP:", e)
        utime.sleep_ms(3000)