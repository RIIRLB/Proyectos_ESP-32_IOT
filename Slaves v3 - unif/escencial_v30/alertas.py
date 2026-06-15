# alertas.py — STUB (histéresis -> PASO 4; gestor de estado híbrido -> PASO 6)
#
# Responsabilidad (tabla 5.1): evaluar_alertas(), histéresis WARN/CRIT,
# jitter, y la decisión de estado SUPER-LIGHT / NORMAL / ALERTA.

_umbrales = {}
_estado   = "SUPER-LIGHT"   # arranca en el estado de ahorro (sección 4.1)


def iniciar(umbrales):
    """Carga la tabla de umbrales (config.UMBRALES)."""
    global _umbrales
    _umbrales = umbrales or {}
    # TODO paso 4: inicializar estado de histéresis por sensor


def estado_actual():
    """Estado del modo híbrido: 'SUPER-LIGHT' | 'NORMAL' | 'ALERTA'."""
    return _estado          # TODO paso 6: lógica real de transición


def evaluar_alertas(med):
    """Recibe la lista de mediciones; devuelve (nivel, sensores_afectados).

    nivel: None | 'WARN' | 'CRIT'
    """
    return None, []         # TODO paso 4
