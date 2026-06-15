# main.py — orquestación del slave PIF Mesh (modo NORMAL, siempre despierto).
#
# Replica el comportamiento usable de tu v12.4, pero modular:
#   - arranca, detecta sensores, se une a la malla y busca al master
#   - mide y manda FB por su cuenta cada FB_PERIODO_S
#   - responde de inmediato a un REQ del master (WAVE)
#   - reenvía (relay) los FB de otros nodos -> malla multi-salto
#   - evalúa alertas con histéresis y las muestra/envía
#   - rota la pantalla (ambiente / aire / movimiento) y atiende botones
#   - si pierde al master, re-escanea canales en segundo plano
#
# El modo híbrido de bajo consumo (dormir entre lecturas) es una MEJORA
# posterior (pasos 6-8). Este build no duerme: es fácil de usar y depurar.

import gc, utime
from utime import ticks_ms, ticks_diff
import config
import ui
from sens import Sensores
from mesh import Malla

gc.collect()

# ───────────────────────────────────────────────
#  ★ ÚNICO PARÁMETRO A CAMBIAR POR DISPOSITIVO ★
#    SLAVE_01, SLAVE_02, ... (único en toda la malla)
# ───────────────────────────────────────────────
NODE_ID = "SLAVE_01"

SIN_MASTER_MS = 15000      # sin BEACON del master por 15s -> desconectado


def arrancar():
    print("=" * 40)
    print("PIF Mesh slave —", NODE_ID)
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

    # buscar al master un par de pasadas (si no, el loop re-escanea solo)
    for _ in range(2):
        if malla.escanear_canal(config.CANAL_SCAN_MS):
            break

    print("sensores:", sensores.activos, "| master:", malla.conectado)
    print("=" * 40)
    return sensores, malla


def medir_y_enviar(sensores, malla, vista, conectado):
    """Mide, evalúa alertas, manda FB y refresca la pantalla."""
    med, lec = sensores.leer()
    nivel, afect = sensores.evaluar_alertas(med)
    malla.mandar_fb(med, alerta=nivel, a_t=afect, reps=config.FB_REPS)
    hora = malla.hora_hhmmss()
    if nivel:
        ui.mostrar_alerta(NODE_ID, nivel, afect, lec, hora)
    else:
        ui.mostrar_normal(NODE_ID, lec, hora, conectado, vista)
    print("[FB] {} nivel={} pl={}".format(hora, nivel, len(med)))
    return lec, nivel, afect


def _redibujar(lec, nivel, afect, conectado, vista, malla):
    hora = malla.hora_hhmmss()
    if nivel:
        ui.mostrar_alerta(NODE_ID, nivel, afect, lec, hora)
    else:
        ui.mostrar_normal(NODE_ID, lec, hora, conectado, vista)


def main():
    sensores, malla = arrancar()

    t_fb = t_vista = t_rescan = ticks_ms()
    t_wave = ticks_ms() if malla.conectado else 0
    vista = 0
    iz_prev = de_prev = 1

    conectado = malla.conectado
    lec, nivel, afect = medir_y_enviar(sensores, malla, vista, conectado)
    t_fb = ticks_ms()

    while True:
        ahora = ticks_ms()

        # 1) tráfico entrante
        d = malla.recibir(50)
        if d:
            tp = d.get("type")
            if tp == "WAVE":
                if d.get("from") == config.MASTER_ID:
                    t_wave = ahora                      # beacon del master visto
                if malla.manejar_wave(d):               # REQ para mí / ALL
                    cmd = d.get("cmd", "")
                    if cmd in ("DORMIR", "ACTIVAR"):
                        malla.mandar_ack(cmd)           # reconoce; no duerme (build NORMAL)
                    else:
                        lec, nivel, afect = medir_y_enviar(sensores, malla, vista, conectado)
                        t_fb = ahora
            elif tp == "FB":
                malla.relay_fb(d)                       # reenvía FB ajeno (multi-salto)

        # estado de conexión por recencia del beacon
        conectado = (t_wave != 0) and (ticks_diff(ahora, t_wave) < SIN_MASTER_MS)

        # 2) FB autónomo periódico
        if ticks_diff(ahora, t_fb) >= config.FB_PERIODO_S * 1000:
            lec, nivel, afect = medir_y_enviar(sensores, malla, vista, conectado)
            t_fb = ahora

        # 3) botones (flanco 1->0, activo en bajo)
        iz = ui.btn_izq.value()
        de = ui.btn_der.value()
        if iz_prev == 1 and iz == 0:                    # izq: luz + rota vista
            ui.backlight_on()
            vista = (vista + 1) % 3
            _redibujar(lec, nivel, afect, conectado, vista, malla)
            t_vista = ahora
        if de_prev == 1 and de == 0:                    # der: medición on-demand
            ui.backlight_on()
            lec, nivel, afect = medir_y_enviar(sensores, malla, vista, conectado)
            t_fb = ahora
        iz_prev, de_prev = iz, de

        # 4) rotación automática de vista
        if ticks_diff(ahora, t_vista) >= config.VISTA_ROT_S * 1000:
            vista = (vista + 1) % 3
            if not nivel:
                ui.mostrar_normal(NODE_ID, lec, malla.hora_hhmmss(), conectado, vista)
            t_vista = ahora

        # 5) re-escaneo si se perdió el master
        if not conectado and ticks_diff(ahora, t_rescan) >= config.RESCAN_S * 1000:
            print("[MAIN] master perdido -> re-escaneando canales")
            if malla.escanear_canal(config.CANAL_SCAN_MS):
                t_wave = ticks_ms()
            t_rescan = ahora

        gc.collect()
        utime.sleep_ms(20)


if __name__ == "__main__":
    main()
