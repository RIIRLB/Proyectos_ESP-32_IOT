# Slave_Node_v13.2.py — slave PIF Mesh en MODO BAJO CONSUMO.
#   ▸ Súbelo al dispositivo COMO main.py (para que arranque solo).
#   ▸ Cambia NODE_ID por dispositivo (único en toda la malla).
#
# CÓMO FUNCIONA (ventana sincronizada):
#   - El nodo duerme en lightsleep y despierta una VENTANA corta cada
#     WAKE_PERIODO_MS, fijándose al beacon del master (pulso de sincronía).
#     Como TODOS despiertan a la vez, un mensaje del fondo puede subir varios
#     saltos dentro de la misma ventana.
#   - En la ventana enciende la radio y escucha:
#       · REQ del master para mí/ALL  -> mide y envía (con display).
#       · FB ajeno y voy "cuesta arriba" hacia el master -> lo retransmito y
#         muestro "Msg de X / ENVIANDO…-ENVIADO".
#   - Cada FB_CADA_MS hace una medición rutinaria por su cuenta (para el panel).
#     En ALERTA reporta cada ALERTA_CADA_MS.
#   - El BOTÓN despierta por hardware (medición on-demand con display).
#   - Display/backlight apagados mientras duerme.
#
# ⚠ EXPERIMENTAL: el estado de ESP-NOW tras lightsleep depende del firmware
#   MicroPython. Aquí re-activamos la radio al despertar (malla.reactivar()).
#   Esto y el wake por botón hay que VERIFICARLOS en hardware (ver README).

import gc, utime, machine
from utime import ticks_ms, ticks_diff, ticks_add
from machine import Pin, lightsleep
try:
    import esp32
except ImportError:
    esp32 = None

import config
import ui
from sens import Sensores
from mesh import Malla

gc.collect()

# ───────────────────────────────────────────────
#  ★ ÚNICO PARÁMETRO A CAMBIAR POR DISPOSITIVO ★
# ───────────────────────────────────────────────
NODE_ID = "SLAVE_02"

# estado de cadencia (módulo)
_t_fb = 0
_vista = 0


def arrancar():
    print("=" * 40)
    print("PIF Mesh slave (bajo consumo) —", NODE_ID)
    print("NET:", config.NET_ID, "| MASTER:", config.MASTER_ID)
    ui.init(rotation=1)
    ui.backlight_on()
    ui.boot(NODE_ID)

    sensores = Sensores(pin_dht=config.PIN_DHT, pin_mq=config.PIN_MQ,
                        scl=config.PIN_I2C_SCL, sda=config.PIN_I2C_SDA,
                        umbrales=config.UMBRALES)
    sensores.detectar()

    malla = Malla(NODE_ID, net_id=config.NET_ID, master_id=config.MASTER_ID,
                  canales=config.CANALES_SCAN)
    malla.iniciar()
    malla.fijar_canal(config.CANAL_FIJO)        # canal 4 primero (como pediste)

    sincronizar(malla)                          # esperar el primer beacon
    print("=" * 40)
    return sensores, malla


def sincronizar(malla):
    """Despierto, escucha hasta oír un beacon del master (sync RTC + canal +
    distancia). Si no aparece en SYNC_TIMEOUT_MS en el canal fijo, escanea
    como respaldo (por si el router no está en el canal 4)."""
    print("[SYNC] esperando beacon del master en ch", malla.canal)
    fin = ticks_add(ticks_ms(), config.SYNC_TIMEOUT_MS)
    while ticks_diff(fin, ticks_ms()) > 0:
        d = malla.recibir(80)
        if d and d.get("type") == "WAVE" and d.get("from") == config.MASTER_ID:
            malla.manejar_wave(d)
            print("[SYNC] master OK ch:", malla.canal, "| dist:", malla.dist_master)
            return True
    print("[SYNC] sin beacon en ch fijo -> escaneo de respaldo")
    if malla.escanear_canal(config.CANAL_SCAN_MS):
        return True
    print("[SYNC] master no encontrado; reintento en cada ventana")
    return False


def medir_y_enviar(sensores, malla):
    """Mide, evalúa alertas, manda FB (con mid único + gradiente) y refresca display."""
    global _vista
    ui.backlight_on()
    med, lec = sensores.leer()
    nivel, afect = sensores.evaluar_alertas(med)
    malla.mandar_fb(med, mid=malla.next_mid(), alerta=nivel, a_t=afect,
                    reps=config.FB_REPS)
    hora = malla.hora_hhmmss()
    if nivel:
        ui.mostrar_alerta(NODE_ID, nivel, afect, lec, hora)
    else:
        ui.mostrar_normal(NODE_ID, lec, hora, malla.conectado, _vista)
        _vista = (_vista + 1) % 3
    print("[FB]", hora, "| nivel:", nivel, "| dist:", malla.dist_master)
    return nivel


def _boton_presionado():
    iz = ui.btn_izq.value() if ui.btn_izq else 1
    de = ui.btn_der.value() if ui.btn_der else 1
    return iz == 0 or de == 0


def ventana(sensores, malla):
    """Una ventana activa: radio encendida, atiende tráfico y mide si toca."""
    global _t_fb
    malla.reactivar()                           # re-asegurar radio tras dormir

    # botón presionado al despertar -> medición on-demand inmediata
    if _boton_presionado():
        print("[BTN] on-demand")
        medir_y_enviar(sensores, malla)
        _t_fb = ticks_ms()

    fin = ticks_add(ticks_ms(), config.VENTANA_MS)
    while ticks_diff(fin, ticks_ms()) > 0:
        d = malla.recibir(30)
        if not d:
            continue
        tp = d.get("type")
        if tp == "WAVE":
            if malla.manejar_wave(d):                 # REQ para mí / ALL
                cmd = d.get("cmd", "")
                if cmd in ("DORMIR", "ACTIVAR"):
                    malla.mandar_ack(cmd)
                elif cmd.startswith("REQ"):
                    medir_y_enviar(sensores, malla)
                    _t_fb = ticks_ms()
        elif tp == "FB":
            if malla.relay_fb(d):                     # retransmití hacia el master
                origen = d.get("id", "?")
                ui.backlight_on()
                ui.mostrar_relay(origen, "ENVIANDO...")
                ui.mostrar_relay(origen, "ENVIADO")   # el envío ya ocurrió en relay_fb

    # medición rutinaria por cadencia (NORMAL vs ALERTA)
    en_alerta = (sensores.estado_actual() == "ALERTA")
    cadencia = config.ALERTA_CADA_MS if en_alerta else config.FB_CADA_MS
    if _t_fb == 0 or ticks_diff(ticks_ms(), _t_fb) >= cadencia:
        medir_y_enviar(sensores, malla)
        _t_fb = ticks_ms()


def _calc_sleep(malla):
    """ms a dormir hasta justo antes del próximo beacon esperado."""
    if malla.ultimo_beacon:
        prox = ticks_add(malla.ultimo_beacon, config.WAKE_PERIODO_MS)
        dur = ticks_diff(prox, ticks_ms()) - config.GUARDA_MS
        while dur <= 0:                              # normaliza a (0, WAKE_PERIODO]
            dur += config.WAKE_PERIODO_MS
        return dur
    return config.WAKE_PERIODO_MS - config.VENTANA_MS


def dormir(malla):
    """Apaga display y duerme en lightsleep; el botón puede despertar por HW."""
    ui.backlight_off()
    dur = _calc_sleep(malla)
    # botón como fuente de despertar (best-effort; verificar en hardware)
    for b in (ui.btn_izq, ui.btn_der):
        if b is None:
            continue
        try:
            b.irq(trigger=Pin.IRQ_FALLING, wake=machine.SLEEP)
        except Exception as e:
            print("[SLEEP] wake por botón no disponible:", e)
    try:
        lightsleep(dur)
    except Exception as e:
        print("[SLEEP] lightsleep falló, uso sleep_ms:", e)
        utime.sleep_ms(dur)
    for b in (ui.btn_izq, ui.btn_der):              # limpiar irq tras despertar
        if b is None:
            continue
        try:
            b.irq(handler=None)
        except Exception:
            pass


def main():
    sensores, malla = arrancar()
    medir_y_enviar(sensores, malla)                 # primera lectura al arrancar
    global _t_fb
    _t_fb = ticks_ms()
    while True:
        ventana(sensores, malla)
        dormir(malla)
        gc.collect()


if __name__ == "__main__":
    main()
