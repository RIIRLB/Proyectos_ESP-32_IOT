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
PIN_MQ        = 33     # MQ135 ADC1_CH5. Recomendado 33 (deja libre el 34,
                       #   que en la TTGO va al divisor de batería / VBAT).
                       #   Mueve el cable del MQ de GPIO34 -> GPIO33.
PIN_I2C_SDA   = 21     # MPU6050 (y MLX si algún día vuelve)
PIN_I2C_SCL   = 22     # MPU6050
PIN_BTN_LEFT  = 0      # Acción / pulsación larga 3s = cambia modo
PIN_BTN_RIGHT = 35     # Medición on-demand
PIN_BACKLIGHT = 4      # Control de luz del display (no está en Anexo A; va aquí por orden)

# ──── Modo HÍBRIDO de bajo consumo (ventana sincronizada) ────
# El slave duerme en lightsleep y despierta una ventana corta cada
# WAKE_PERIODO_MS, fijándose al beacon del master (pulso de sincronía).
# Como TODOS despiertan a la vez, el multi-salto cabe dentro de la ventana.
WAKE_PERIODO_MS    = 5000           # cada cuánto despierta (= beacon del master)
VENTANA_MS         = 600            # radio encendida por ventana (cabe varios saltos)
GUARDA_MS          = 90             # despierta un pelín antes del beacon esperado
FB_CADA_MS         = 15000          # medición rutinaria autónoma (NORMAL)
ALERTA_CADA_MS     = 10000          # en ALERTA reporta más seguido
COOLDOWN_ALERTA_MS = 7 * 60 * 1000  # 7 min sin nuevo umbral -> sale de ALERTA
DISPLAY_MS         = 4000           # display encendido tras un evento
SYNC_TIMEOUT_MS    = 15000          # sin beacon -> intenta resincronizar/escanear
FB_REPS            = 2              # repeticiones de cada FB (robustez ESP-NOW)

# ──── Canal ────
# Tu master usa el canal de tu WiFi (WiFi.channel()), NO un 4 fijo por código.
# CANAL_FIJO se intenta primero; si en SYNC_TIMEOUT_MS no aparece el master,
# se escanea como respaldo (por si tu router no está en el canal 4).
CANAL_FIJO     = 4
CANALES_SCAN   = [4, 1, 6, 11, 8, 2, 3, 5, 7, 9, 10]   # respaldo: 4 primero
CANAL_SCAN_MS  = 1000

# ──── Persistencia / modo por defecto ────
CONFIG_FILE  = "node_config.json"
MODO_DEFAULT = "NORMAL"             # arranca reportando; ALERTA se activa sola

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
