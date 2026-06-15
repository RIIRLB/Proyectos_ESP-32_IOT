# config.py — constantes del slave PIF Mesh / LAB-ARTE
#
# Responsabilidad (tabla 5.1 del doc): NET_ID, MASTER_ID, umbrales,
# tiempos, pines, modo híbrido. SOLO constantes; sin lógica.
# Los valores son sugerencias iniciales (Anexo A). Cada slave puede
# sobrescribir lo que necesite desde main.py.

# ──── Identidad de red ────
# Deben ser IDÉNTICOS en master y en todos los slaves.
NET_ID    = "PIFNET"
MASTER_ID = "MASTER_TTGO_GATEWAY"

# ──── Pines (ver Anexo C — Tabla de pines TTGO T-Display) ────
PIN_DHT       = 15     # DHT11 data
PIN_MQ        = 33     # MQ135 ADC.  ⚠ OJO: slave v12.4 usa 34; el doc/Ana usan 33.
                       #   Confirmar cuál está físicamente cableado.
PIN_I2C_SDA   = 21     # MLX90614 + MPU6050
PIN_I2C_SCL   = 22     # MLX90614 + MPU6050
PIN_BTN_LEFT  = 0      # Acción / pulsación larga 3s = cambia modo
PIN_BTN_RIGHT = 35     # Medición on-demand
PIN_BACKLIGHT = 4      # Control de luz del display (no está en Anexo A; va aquí por orden)

# ──── Modo híbrido (sección 4 del doc) ────
SUPER_CHECK_S       = 5      # micro-check cada 5 s en SUPER-LIGHT
PERIODO_NORMAL_S    = 15     # ventana de medición cada 15 s
ALERTA_PERIODO_MS   = 10000  # FB cada 10 s en ALERTA
ALERTA_JITTER_PCT   = 30     # ±30% jitter para no saturar el canal
COOLDOWN_ALERTA_MIN = 7      # minutos sin nuevo umbral antes de salir de ALERTA

# ──── Canales (sección 6 — verificación robusta) ────
# Orden: los más comunes primero para encontrar al master más rápido.
CANALES_SCAN  = [1, 6, 11, 4, 8, 2, 3, 5, 7, 9, 10]
CANAL_SCAN_MS = 1200    # 1.2 s por canal
RESCAN_BG_MIN = 5       # re-escaneo en segundo plano cada 5 min (nunca rendirse)

# ──── Persistencia en flash (sección 6.1) ────
CONFIG_FILE  = "node_config.json"
MODO_DEFAULT = "SUPER-LIGHT"   # estado híbrido por defecto si no hay node_config.json

# ──── Umbrales con histéresis (sensor: {warn, warn_sale, crit, crit_sale}) ────
# 'warn'/'crit' = nivel que ENTRA en alerta; '*_sale' = nivel que la libera.
# La separación entre entrar y salir es la histéresis (evita parpadeo).
UMBRALES = {
    "Temp":     {"warn": 45.0, "warn_sale": 42.0,
                 "crit": 60.0, "crit_sale": 55.0},
    "Hum":      {"bajo": 15.0, "bajo_sale": 18.0,
                 "bajo_crit": 8.0, "bajo_crit_sale": 11.0},
    "MQ135":    {"warn": 2500, "warn_sale": 2200,
                 "crit": 3500, "crit_sale": 3200},
    "Temp_obj": {"warn": 38.0, "warn_sale": 37.0,
                 "crit": 39.5, "crit_sale": 38.5},
}
