# mesh.py — capa de malla ESP-NOW del slave PIF Mesh.
#
# Portado del malla.py de Ana. Es EXACTAMENTE el protocolo que tu
# master_ttgo (v18.8) habla: WAVE/FB en JSON con "net"="PIFNET", "mid",
# "ttl", "ch", "ts". El master ignora cualquier paquete con otro "net".
#
#   WAVE: {"type":"WAVE","cmd":..,"from":..,"target":..,"ttl":6,"ch":N,
#          "mid":N,"ts":"YYYY-MM-DD HH:MM:SS"}
#   FB:   {"type":"FB","net":..,"id":..,"par":..,"pl":[{"t":..,"v":..}],
#          "mid":N, "alert":.., "a_t":[..]}

import gc, network, espnow, json, time
from machine import RTC
from time import ticks_ms, ticks_add, ticks_diff

BROADCAST = b'\xff\xff\xff\xff\xff\xff'
CANALES_DEFAULT = [1, 6, 11, 4, 8, 2, 3, 5, 7, 9, 10]
DEDUP_TTL_MS = 30_000


class Malla:
    def __init__(self, node_id, net_id="PIFNET",
                 master_id="MASTER_TTGO_GATEWAY",
                 canales=None, relay=True, mid_base=0):
        self.node_id   = node_id
        self.net_id    = net_id
        self.master_id = master_id
        self.canales   = canales or CANALES_DEFAULT
        self.relay     = relay
        self._mid      = mid_base

        self.sta = network.WLAN(network.STA_IF)
        self.en  = None
        self.canal = self.canales[0]
        self.conectado    = False
        self.ultimo_padre = master_id
        self.dist_master   = 99      # saltos al master (99 = aún no lo sé; 0 = soy master)
        self.ultimo_beacon = 0       # ticks_ms del último beacon del master (sincronía)

        self._waves = {}     # dedup WAVE  {mid: ticks}
        self._fbs   = {}     # dedup FB    {"id|mid": ticks}

        self.rtc = RTC()
        self.hora_ok = False

    # ── RADIO ──────────────────────────────────────────────
    def iniciar(self, canal=None):
        """Abre STA + ESP-NOW. Idempotente: reusa 'en' si ya existe."""
        self.sta.active(True)
        try:                              # apagar power-save (coexistir con ESP-NOW)
            self.sta.config(pm=0xa11140)
        except Exception:
            pass
        if canal is not None:
            try:
                self.sta.config(channel=canal); self.canal = canal
            except Exception:
                pass
        if self.en is None:
            self.en = espnow.ESPNow()
            self.en.active(True)
            self.en.add_peer(BROADCAST)
        return self.en

    def cerrar(self):
        """Cierra ESP-NOW + STA. Usar solo antes de lightsleep/deepsleep."""
        if self.en:
            try: self.en.active(False)
            except Exception: pass
            self.en = None
        try: self.sta.active(False)
        except Exception: pass
        gc.collect()

    # ── ESCANEO DE CANAL (in-situ, no reabre 'en') ─────────
    def escanear_canal(self, ms=1200):
        """Recorre canales buscando un WAVE/BEACON del master REAL. True si lo halla."""
        if self.en is None:
            self.iniciar()
        for ch in self.canales:
            try: self.sta.config(channel=ch)
            except Exception: continue
            time.sleep_ms(120)
            fin = ticks_add(ticks_ms(), ms)
            while ticks_diff(fin, ticks_ms()) > 0:
                d = self.recibir(50)
                if (d and d.get("type") == "WAVE"
                        and d.get("from") == self.master_id):
                    self.canal = d.get("ch", ch)
                    try: self.sta.config(channel=self.canal)
                    except Exception: pass
                    self.conectado = True
                    self._sync_rtc(d.get("ts"))
                    print("[MALLA] master en canal", self.canal)
                    return True
            gc.collect()
        print("[MALLA] master no encontrado, ch:", self.canal)
        return False

    # ── RECEPCIÓN ──────────────────────────────────────────
    def recibir(self, timeout=50):
        """Devuelve un dict (ya filtrado por net) o None."""
        if self.en is None:
            return None
        try:
            _, msg = self.en.recv(timeout)
        except Exception:
            return None
        if not msg:
            return None
        try:
            d = json.loads(msg.decode())
        except Exception:
            return None
        if d.get("net") != self.net_id:
            return None
        return d

    # ── DEDUP ──────────────────────────────────────────────
    def _visto(self, tabla, clave):
        ahora = ticks_ms()
        for k in [k for k, v in tabla.items()
                  if ticks_diff(ahora, v) > DEDUP_TTL_MS]:
            del tabla[k]
        if clave in tabla:
            return True
        tabla[clave] = ahora
        return False

    # ── MANEJO DE WAVE ─────────────────────────────────────
    def manejar_wave(self, d):
        """Dedup + distancia en saltos + canal del master + relay.
        True si debo responder (REQ para mí / ALL)."""
        mid = d.get("mid")
        if self._visto(self._waves, mid):
            return False
        self._sync_rtc(d.get("ts"))
        frm = d.get("from", self.master_id)
        # ── distancia en saltos: 'h' = distancia del emisor; yo estoy a h+1 ──
        h = d.get("h")
        if h is not None and (h + 1) < self.dist_master:
            self.dist_master = h + 1
        if frm == self.master_id:                  # beacon/WAVE del master real
            self.ultimo_beacon = ticks_ms()        # pulso de sincronía
            ch = d.get("ch", self.canal)
            if ch != self.canal:
                self.canal = ch
                try: self.sta.config(channel=ch)
                except Exception: pass
        self.conectado    = True
        self.ultimo_padre = frm
        if self.relay and d.get("ttl", 0) > 1:
            self._relay_wave(d)
        target = d.get("target", "ALL")
        return target == "ALL" or target == self.node_id

    def _relay_wave(self, d):
        nuevo = dict(d)
        nuevo["from"] = self.node_id
        nuevo["ttl"]  = d["ttl"] - 1
        nuevo["h"]    = self.dist_master           # mi distancia, para el siguiente salto
        try: self.en.send(BROADCAST, json.dumps(nuevo).encode())
        except Exception: pass

    # ── ENVÍO DE FB ────────────────────────────────────────
    def mandar_fb(self, payload, parent=None, mid=None,
                  alerta=None, a_t=None, wake_path=False, reps=2):
        pkt = {"type": "FB", "net": self.net_id, "id": self.node_id,
               "par": parent or self.ultimo_padre, "pl": payload,
               "uh": self.dist_master}        # gradiente: mi distancia al master
        if mid is not None:
            pkt["mid"] = mid
        if alerta:
            pkt["alert"] = alerta
            pkt["a_t"]   = a_t or []
        if wake_path:                              # reservado para el paso 8
            pkt["wp"] = True
        s = json.dumps(pkt)
        if len(s) > 248:                           # recortar si excede ESP-NOW
            pkt["pl"] = payload[:3]
            s = json.dumps(pkt)
        ok = False
        for _ in range(reps):
            try: ok = bool(self.en.send(BROADCAST, s.encode())) or ok
            except Exception: pass
            time.sleep_ms(120)
        flag = " [" + alerta + "]" if alerta else ""
        print("[>>> FB TX] {} -> {}  mid:{}  bytes:{}  hw:{}{}".format(
            self.node_id, pkt["par"], mid, len(s), "OK" if ok else "FALLO", flag))
        return ok

    def mandar_ack(self, cmd, parent=None):
        """Confirma un comando (DORMIR/ACTIVAR) al master."""
        return self.mandar_fb([{"t": "ACK", "v": cmd}], parent=parent, reps=2)

    # ── ENVÍO DE WAVE (si este nodo origina órdenes) ───────
    def next_mid(self):
        self._mid += 1
        return self._mid

    def mandar_wave(self, cmd="REQ:ALL", target="ALL", reps=3):
        pkt = {"type": "WAVE", "net": self.net_id, "cmd": cmd,
               "from": self.node_id, "target": target, "ttl": 6,
               "ch": self.canal, "mid": self.next_mid(), "ts": self.ts_actual()}
        s = json.dumps(pkt)
        for _ in range(reps):
            try: self.en.send(BROADCAST, s.encode())
            except Exception: pass
            time.sleep_ms(120)
        return pkt["mid"]

    # ── RELAY DE FB AJENO (direccional: solo "cuesta arriba" al master) ──
    def relay_fb(self, d):
        """Retransmite un FB ajeno SOLO si estoy más cerca del master que quien
        lo emitió (gradiente 'uh'). Así solo el camino hacia el master reenvía.
        Devuelve True si retransmití, False si no."""
        if not self.relay:
            return False
        idn = d.get("id")
        mid = d.get("mid")
        if idn == self.node_id:
            return False
        # gradiente: solo reenvío si me acerco al master
        uh = d.get("uh", 99)
        if self.dist_master >= uh:
            return False
        if self._visto(self._fbs, "{}|{}".format(idn, mid)):
            return False
        via = d.get("via", [])
        if self.node_id in via:
            return False
        via.append(self.node_id)
        d["via"] = via
        d["uh"]  = self.dist_master          # actualizo el gradiente para el siguiente
        try:
            s = json.dumps(d)
            if len(s) < 248:
                self.en.send(BROADCAST, s.encode())
                return True
        except Exception:
            pass
        return False

    # ── HORA / RTC ─────────────────────────────────────────
    def _sync_rtc(self, ts):
        if not ts or not isinstance(ts, str):
            return
        try:
            f, h = ts.split(" ")
            a, m, d = [int(x) for x in f.split("-")]
            hh, mm, ss = [int(x) for x in h.split(":")]
            self.rtc.datetime((a, m, d, 0, hh, mm, ss, 0))
            self.hora_ok = True
        except Exception:
            pass

    def ts_actual(self):
        lt = time.localtime()
        if self.hora_ok:
            return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*lt[:6])
        return "{:02d}:{:02d}:{:02d}".format(lt[3], lt[4], lt[5])

    def hora_hhmmss(self):
        lt = time.localtime()
        return "{:02d}:{:02d}:{:02d}".format(lt[3], lt[4], lt[5])

    # ── CANAL FIJO / SINCRONÍA (modo bajo consumo) ─────────
    def fijar_canal(self, ch):
        """Fija el canal sin escanear (el master suele estar en el del router)."""
        if self.en is None:
            self.iniciar()
        try:
            self.sta.config(channel=ch)
            self.canal = ch
        except Exception:
            pass

    def reactivar(self):
        """Re-asegura la radio tras un lightsleep (best-effort).
        OJO: el comportamiento de ESP-NOW tras lightsleep depende del firmware;
        hay que verificarlo en hardware."""
        try:
            self.sta.active(True)
            self.sta.config(channel=self.canal)
        except Exception:
            pass
        if self.en is None:
            self.iniciar()
        else:
            try:
                self.en.active(True)
            except Exception:
                pass

    def beacon_reciente(self, ms):
        """True si oí un beacon del master en los últimos 'ms' (hay sincronía)."""
        return self.ultimo_beacon != 0 and ticks_diff(ticks_ms(), self.ultimo_beacon) < ms
