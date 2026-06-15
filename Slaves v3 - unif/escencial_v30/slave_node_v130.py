# main.py — orquestación del slave PIF Mesh.
#
# SKELETON (paso 1, estructura de 6 módulos). Solo NODE_ID + arranque +
# import del árbol completo. El bucle del modo híbrido (SUPER-LIGHT /
# NORMAL / ALERTA) se implementa en los pasos 6-8. Por ahora arranca,
# prende display, lee botones y queda en idle: sirve para verificar que
# TODO el árbol importa y el HW responde.

import gc, utime
import config
import ui                      # fuentes + display + pantallas (fusionados)
from sens import Sensores
from mesh import Malla
import alertas

gc.collect()

# ───────────────────────────────────────────────
#  ★ ÚNICO PARÁMETRO A CAMBIAR POR DISPOSITIVO ★
# ───────────────────────────────────────────────
NODE_ID = "SLAVE_01"


def arrancar():
    print("=" * 40)
    print("PIF Mesh slave — skeleton (6 módulos)")
    print("NODE_ID:", NODE_ID)
    print("NET_ID :", config.NET_ID, "| MASTER:", config.MASTER_ID)

    tft = ui.init(rotation=1)

    sensores = Sensores(pin_dht=config.PIN_DHT, pin_mq=config.PIN_MQ,
                        scl=config.PIN_I2C_SCL, sda=config.PIN_I2C_SDA)
    sensores.detectar()
    print("sensores activos:", sensores.activos)

    malla = Malla(NODE_ID, net_id=config.NET_ID, master_id=config.MASTER_ID,
                  canales=config.CANALES_SCAN)
    malla.iniciar()

    alertas.iniciar(config.UMBRALES)

    ui.backlight_on()
    ui.boot(NODE_ID)
    print("estado inicial:", alertas.estado_actual())
    print("skeleton OK — faltan loops (pasos 6-8)")
    print("=" * 40)
    return sensores, malla


def main():
    sensores, malla = arrancar()
    # Idle de verificación: refleja pulsaciones por serial. Sin lógica de modo.
    while True:
        estado = alertas.estado_actual()
        # TODO paso 6: if estado == "SUPER-LIGHT": loop_super_light(malla, sensores)
        # TODO paso 7: elif estado == "NORMAL":     loop_normal(malla, sensores)
        # TODO paso 8: elif estado == "ALERTA":     loop_alerta(malla, sensores)
        if ui.btn_der is not None and ui.btn_der.value() == 0:
            print("[BTN] derecho (medición on-demand) — TODO paso 7")
            ui.backlight_on()
        utime.sleep_ms(200)


if __name__ == "__main__":
    main()
