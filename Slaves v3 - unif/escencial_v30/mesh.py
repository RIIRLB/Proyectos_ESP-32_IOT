# mesh.py — STUB (se implementa en el PASO 2)
#
# Basado en malla.py de Ana + alertas + wake_path + cooldown (sección 3.1, 4).
# Por ahora solo expone la interfaz que main.py y los loops del modo híbrido
# van a llamar. Todos los métodos son no-ops seguros.

class Malla:
    def __init__(self, node_id, net_id="PIFNET",
                 master_id="MASTER_TTGO_GATEWAY",
                 canales=None, relay=True):
        self.node_id   = node_id
        self.net_id    = net_id
        self.master_id = master_id
        self.canales   = canales or []
        self.relay     = relay
        self.conectado = False
        self.canal     = self.canales[0] if self.canales else 1
        # TODO paso 2: portar STA + ESP-NOW, dedup, RTC de malla.py

    # ── radio ──
    def iniciar(self, canal=None):
        pass            # TODO paso 2

    def escanear_canal(self, ms=1200):
        return False    # TODO paso 2 (+ canal cacheado en paso 9)

    def recibir(self, timeout=50):
        return None     # TODO paso 2

    # ── tráfico ──
    def manejar_wave(self, d):
        return False    # TODO paso 2

    def mandar_fb(self, payload, parent=None, mid=None,
                  alerta=None, a_t=None, wake_path=False, reps=2):
        return False    # TODO paso 2 (wake_path -> paso 8)

    def relay_fb(self, d):
        pass            # TODO paso 2

    # ── cascada wake_path (sección 4.4) ──
    def es_wake_path(self, d):
        return False    # TODO paso 8
