# Slave_Node_v132b.py — slave PIF Mesh en MODO BAJO CONSUMO.
#   ▸ Súbelo al dispositivo COMO main.py (para que arranque solo).
#   ▸ Cambia NODE_ID por dispositivo (único en toda la malla).
#
# CÓMO FUNCIONA (ventana sincronizada):
#   - Duerme en lightsleep y despierta una VENTANA corta cada WAKE_PERIODO_MS,
#     fijándose al beacon del master (pulso de sincronía). Todos despiertan a
#     la vez -> un mensaje del fondo puede subir varios saltos en una ventana.
#   - En la ventana escucha. REQ del master -> mide y envía con display.
#     FB ajeno y voy "cuesta arriba" -> lo retransmito y muestro "Msg de X".
#   - Cada FB_CADA_MS hace una medición rutinaria (para el panel).
#   - BOTÓN IZQUIERDO (GPIO0) = despierta del sueño + medición on-demand con
#     display y confirmación de envío. El derecho (GPIO35) también mide, pero
#     SOLO con la pantalla ya encendida: es input-only sin pull-up interno, por
#     eso NO puede despertar de forma fiable desde lightsleep.
#
# ⚠ EXPERIMENTAL: ESP-NOW tras lightsleep y el wake por botón dependen del
#   firmware MicroPython; verificar en hardware.

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
NODE_ID = "SLAVE_04"

# estado de módulo
_t_fb = 0
_vista = 0
_lec_cache = {}          # últimas lecturas (para pintar mientras mide)


def _fmt_lec(lec):
    """Línea legible de lecturas para la terminal."""
    p = []
    if "t" in lec:  p.append("T:{}".format(lec["t"]))
    if "h" in lec:  p.append("H:{}".format(lec["h"]))
    if "mq" in lec: p.append("MQ:{}".format(lec["mq"]))
    if "ax" in lec: p.append("Ax:{} Ay:{} Az:{}".format(lec["ax"], lec["ay"], lec["az"]))
    return "  ".join(p) if p else "(sin sensores)"


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
    print("[BOOT] sensores activos:", sensores.activos)

    malla = Malla(NODE_ID, net_id=config.NET_ID, master_id=config.MASTER_ID,
                  canales=config.CANALES_SCAN)
    malla.iniciar()
    malla.fijar_canal(config.CANAL_FIJO)        # canal 4 primero (como pediste)

    sincronizar(malla)
    print("=" * 40)
    return sensores, malla


def sincronizar(malla):
    """Escucha hasta oír un beacon del master (sync RTC + canal + distancia).
    Si no aparece en SYNC_TIMEOUT_MS en el canal fijo, escanea de respaldo."""
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


def medir_y_enviar(sensores, malla, motivo="auto"):
    """Mide, evalúa alertas, envía FB y muestra TODO el proceso en pantalla y
    terminal (midiendo -> enviando -> enviado/fallo), como el v12.4."""
    global _vista, _lec_cache
    hora = malla.hora_hhmmss()
    ui.backlight_on()

    # 1) midiendo
    ui.mostrar_normal(NODE_ID, _lec_cache, hora, malla.conectado, _vista,
                      status="Midiendo...", status_col=ui.AMARILLO)
    med, lec = sensores.leer()
    _lec_cache = lec
    nivel, afect = sensores.evaluar_alertas(med)
    print("[MED]", hora, "(" + motivo + ")  ", _fmt_lec(lec),
          "| nivel:", nivel, "| dist:", malla.dist_master)

    # 2) enviando
    ui.mostrar_normal(NODE_ID, lec, hora, malla.conectado, _vista,
                      status="Enviando FB...", status_col=ui.AZUL)
    ok = malla.mandar_fb(med, mid=malla.next_mid(), alerta=nivel, a_t=afect,
                         reps=config.FB_REPS)

    # 3) resultado
    if nivel:
        ui.mostrar_alerta(NODE_ID, nivel, afect, lec, hora)
    else:
        if not ok:
            st, col = "FALLO TX", ui.ROJO
        elif malla.conectado:
            st, col = "Enviado", ui.VERDE
        else:
            st, col = "Enviado (sin master?)", ui.AMARILLO
        ui.mostrar_normal(NODE_ID, lec, hora, malla.conectado, _vista,
                          status=st, status_col=col)
        _vista = (_vista + 1) % 3
    return nivel, ok


def _boton_pulsado():
    iz = (ui.btn_izq.value() == 0) if ui.btn_izq else False
    de = (ui.btn_der.value() == 0) if ui.btn_der else False
    return iz or de


def _atender_boton(sensores, malla):
    """Si hay un botón pulsado, medición on-demand + antirrebote. True si atendió."""
    global _t_fb
    if not _boton_pulsado():
        return False
    print("[BOTON] medición on-demand")
    medir_y_enviar(sensores, malla, motivo="boton")
    _t_fb = ticks_ms()
    # antirrebote: esperar a que suelten
    while _boton_pulsado():
        utime.sleep_ms(40)
    return True


def ventana(sensores, malla):
    """Ventana activa: radio encendida, atiende botón + tráfico, mide si toca."""
    global _t_fb
    malla.reactivar()                           # re-asegurar radio tras dormir

    _atender_boton(sensores, malla)             # ¿despertó/pulsaron el botón?

    fin = ticks_add(ticks_ms(), config.VENTANA_MS)
    while ticks_diff(fin, ticks_ms()) > 0:
        if _atender_boton(sensores, malla):     # también atiende pulsaciones en vivo
            continue
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
                    print("[RX] REQ del master -> respondo")
                    medir_y_enviar(sensores, malla, motivo="REQ")
                    _t_fb = ticks_ms()
        elif tp == "FB":
            if malla.relay_fb(d):                     # retransmití hacia el master
                origen = d.get("id", "?")
                print("[RELAY] reenvío FB de", origen, "->", malla.ultimo_padre)
                ui.backlight_on()
                ui.mostrar_relay(origen, "ENVIANDO...")
                ui.mostrar_relay(origen, "ENVIADO")

    # medición rutinaria por cadencia (NORMAL vs ALERTA)
    en_alerta = (sensores.estado_actual() == "ALERTA")
    cadencia = config.ALERTA_CADA_MS if en_alerta else config.FB_CADA_MS
    if _t_fb == 0 or ticks_diff(ticks_ms(), _t_fb) >= cadencia:
        medir_y_enviar(sensores, malla, motivo="rutina")
        _t_fb = ticks_ms()


def _calc_sleep(malla):
    """ms a dormir hasta justo antes del próximo beacon esperado."""
    if malla.ultimo_beacon:
        prox = ticks_add(malla.ultimo_beacon, config.WAKE_PERIODO_MS)
        dur = ticks_diff(prox, ticks_ms()) - config.GUARDA_MS
        while dur <= 0:
            dur += config.WAKE_PERIODO_MS
        return dur
    return config.WAKE_PERIODO_MS - config.VENTANA_MS


def dormir(malla):
    """Apaga display y duerme. El botón IZQUIERDO (GPIO0) despierta por HW."""
    ui.backlight_off()
    dur = _calc_sleep(malla)
    # wake por botón izquierdo (GPIO0 tiene pull-up interno -> fiable en sleep)
    if esp32 is not None and ui.btn_izq is not None:
        try:
            esp32.wake_on_ext0(pin=ui.btn_izq, level=esp32.WAKEUP_ALL_LOW)
        except Exception as e:
            print("[SLEEP] wake_on_ext0 no disponible:", e)
    try:
        lightsleep(dur)
    except Exception as e:
        print("[SLEEP] lightsleep falló, uso sleep_ms:", e)
        utime.sleep_ms(dur)


def main():
    sensores, malla = arrancar()
    medir_y_enviar(sensores, malla, motivo="arranque")
    global _t_fb
    _t_fb = ticks_ms()
    while True:
        ventana(sensores, malla)
        dormir(malla)
        gc.collect()


if __name__ == "__main__":
    main()
