// ============================================================
//  MASTER_TTGO v19.0 — PIF Mesh / LAB-ARTE
//
//  Cambios vs v18.7:
//    [FIX CRÍTICO] BEACON cada 3 segundos (antes 60s). El master está
//                  clavado en el canal del router (no puede cambiarlo sin
//                  perder WiFi). Los slaves barren 11 canales buscándolo.
//                  Con BEACON cada 60s, la probabilidad de que un slave
//                  estuviera escuchando el canal correcto justo cuando
//                  salía el BEACON era mínima → no se encontraban.
//                  Con BEACON cada 3s, el slave coincide en pocos
//                  segundos sin importar en qué canal esté barriendo.
//                  El BEACON es un paquete diminuto con TTL bajo, así que
//                  el tráfico extra es despreciable.
//
//  Cambios vs v18.6:
//    [NUEVO] Identificador de red NET_ID="PIFNET" en todos los WAVE/FB.
//            El master ignora cualquier paquete entrante cuyo "net" no
//            coincida. Esto evita que se mezcle con la malla de otra
//            persona que use ESP-NOW cerca (interferencia entre proyectos). 
//            Debe coincidir con el NET_ID de los slaves (v12.3+).
//
//  Cambios vs v18.5:
//    [FIX] El master ya NO mide su DHT11 cada 60s. Eso era inconsistente
//          con los slaves (que miden cada 10 min) y saturaba la tabla
//          del servidor.
//          Ahora:
//            - Cada T_HB_MQTT (60s): heartbeat = STATUS + PULSE (sin medición).
//              El servidor usa esto para saber que el master está vivo.
//            - Cada T_MEDICION_PROPIA (10 min): mide DHT11 y publica.
//              Igual cadencia que los slaves.
//            - Botón derecho / REQ:ALL / arranque: mide on-demand.
//            - Modo alerta (cuando T>=45°C o H<=15%): manda cada ~10s.
//              Esto lo controla evaluarAlertasMaster() en v18.5.
//
//  Cambios vs v18.4:
//    [PREV] Propagación de alertas de slaves al servidor (línea ALERT).
//    [PREV] Alertas propias del master con histéresis (45/60°C, 15/8%).
//  Arduino C++ port — TTGO T-Display
//
//  Cambios vs v18.3:
//    [NUEVO 1] Sensor DHT11 propio en pin 15. El master ahora se
//              comporta como un nodo más, publicando sus propias
//              mediciones a MQTT con id="MASTER_TTGO_GATEWAY".
//    [NUEVO 2] Display apagado por defecto. Se prende SOLO en:
//                (a) FB recibido de un slave
//                (b) comando recibido del servidor
//                (c) botón izquierdo (WAVE manual)
//                (d) botón derecho (medición + publicación)
//              Auto-apaga a los T_DISPLAY_ON ms (8000 por defecto).
//              Los heartbeats ya no encienden la pantalla.
//
//  Cambios vs v18.2:
//    [PREV 1] WAVE-beacon automático cada 60s con target="NONE".
//    [PREV 2] Heartbeat STATUS MQTT cada 60s.
//
//  Cambios vs v18.1:
//    [FIX 1] WiFi.setSleep(false) + esp_wifi_set_ps(WIFI_PS_NONE)
//    [FIX 2] peer.channel = canalActual (no 0)
//    [FIX 3] mqttClient.loop() en loop principal
//    [FIX 4] WiFi.mode(WIFI_STA) solo cuando no está ya en STA
//    [FIX 5] Campo "ts" en WAVE — sync de RTC en slaves
//    [FIX 6] Dedup global de mids con TTL
//
//  Protocolo idéntico a slaves MicroPython v11.7+:
//    WAVE: {"type":"WAVE","cmd":"...","from":"...","target":"...",
//           "ttl":6,"ch":N,"mid":N,"ts":"YYYY-MM-DD HH:MM:SS"}
//    FB:   {"type":"FB","id":"...","par":"...","pl":[...],"mid":N,
//           "via":[...]}
//
//  Comunicación con servidor:
//    HTTP GET  /comandos       → recibir comandos pendientes
//    HTTP POST /comandos/ack   → confirmar procesados
//    MQTT publish datos/sensores → enviar mediciones
// ============================================================

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <PubSubClient.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <TFT_eSPI.h>
#include <SPI.h>
#include <DHT.h>          // [NUEVO 1] sensor DHT11 propio

// ───────────────────────────────────────────────
//  CONFIGURACIÓN — ajustar según red activa
// ───────────────────────────────────────────────
// [v18.9] REDES CONOCIDAS — el master prueba cada una en orden hasta
// enganchar. Cada red trae SU propio servidor/broker (cambian entre casa
// y universidad). Para añadir otra red, agrega una línea más al arreglo.
struct RedWiFi {
  const char* ssid;
  const char* pass;
  const char* server_ip;
  int         server_port;
  const char* mqtt_broker;
};
RedWiFi REDES[] = {
  { "Arte_Tenda2.4",  "Lab4rt3#",         "192.168.1.146",   5000, "192.168.1.146"   },
  { "Totalplay-C5AC", "C5AC642BDVePRn6Z", "192.168.100.132", 5000, "192.168.100.132" },
};
const int N_REDES   = sizeof(REDES) / sizeof(REDES[0]);
int       redActiva = -1;   // índice de la red conectada (-1 = ninguna)

// Config ACTIVA: la llena conectarWifi() según la red que enganche.
// Conserva los nombres de antes (SERVER_IP / MQTT_BROKER) para no tocar
// el resto del código que ya los usa.
String      SERVER_IP   = REDES[0].server_ip;
int         SERVER_PORT = REDES[0].server_port;
String      MQTT_BROKER = REDES[0].mqtt_broker;
const int   MQTT_PORT   = 1883;
const char* CLIENT_ID   = "MASTER_TTGO_GATEWAY";
const char* TOPIC_PUB   = "datos/sensores";
// [v18.7] Identificador de red. Debe ser idéntico en master y todos los
// slaves. Los paquetes con otro "net" se ignoran. Esto evita que tu malla
// se mezcle con la de otra persona que use ESP-NOW cerca de ti.
const char* NET_ID      = "PIFNET";

const unsigned long T_MESH_LISTEN  = 5000;   // ms
const unsigned long T_HTTP_POLL    = 3000;   // ms entre polls HTTP
const unsigned long T_HEARTBEAT    = 8000;
const unsigned long T_PANTALLA     = 600000; // 10 min
const unsigned long T_BEACON       = 5000;   // [v19.0] BEACON cada 5s = ventana del slave (pulso de sincronia)
const unsigned long T_HB_MQTT      = 60000;  // heartbeat MQTT cada 60s (solo PULSE+STATUS)
const unsigned long T_MEDICION_PROPIA = 600000;  // [v18.6] DHT11 cada 10 min como slaves
const unsigned long T_CHECK_ALERTA    = 30000;   // [v18.6] check DHT11 para alertas cada 30s
const unsigned long T_DISPLAY_ON   = 8000;
const int           BROADCAST_N    = 3;
const int           TZ_OFFSET_HRS  = -6;

// [v18.9] BOOST — ráfaga fuerte de WAVE (pulsación larga del botón izq).
// Sube la potencia TX al máximo y bombardea WAVE durante ~3s para que un
// slave que esté escaneando enganche al master de inmediato.
const unsigned long T_BOOST_HOLD_MS = 1500;  // ms de pulsación larga p/ disparar boost
const int           BOOST_REPS      = 20;    // nº de WAVE en la ráfaga
const unsigned long BOOST_GAP_MS    = 150;   // separación entre WAVE (20*150 ≈ 3s)

uint8_t BROADCAST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ───────────────────────────────────────────────
//  HARDWARE
// ───────────────────────────────────────────────
#define PIN_BACKLIGHT 4
#define PIN_BTN_LEFT  0
#define PIN_BTN_RIGHT 35
#define PIN_DHT11     15           // [NUEVO 1] sensor propio
#define DHT_TYPE      DHT11

TFT_eSPI tft = TFT_eSPI();
WiFiClient   wifiClient;
PubSubClient mqttClient(wifiClient);
DHT          dhtMaster(PIN_DHT11, DHT_TYPE);   // [NUEVO 1]

// ───────────────────────────────────────────────
//  ESTADO
// ───────────────────────────────────────────────
struct NodoVisto {
  String id;
  String parent;
  String via;
  unsigned long ultimoVisto;
  uint16_t count;
};
const int MAX_NODOS = 15;
NodoVisto nodosVistos[MAX_NODOS];
int nodosCount = 0;

const int MAX_COLA_SUBIDA = 30;
String colaSubida[MAX_COLA_SUBIDA];
int colaSubidaCount = 0;

const int MAX_COLA_BAJADA = 10;
String colaBajada[MAX_COLA_BAJADA];
int colaBajadaCount = 0;

uint32_t msgCounter = 0;
int      canalActual = 1;
bool     wifiOk = false;
bool     mqttOk = false;
unsigned long ultimoHttpPoll = 0;
unsigned long ultimoHeartbeat = 0;
unsigned long ultimaPantalla = 0;
unsigned long ultimoBeacon = 0;
unsigned long ultimoHbMqtt = 0;
unsigned long ultimaMedicionPropia = 0;   // [v18.6] medición DHT11 cada 10 min
unsigned long ultimoCheckAlerta = 0;      // [v18.6] check DHT11 para alertas cada 30s

// [NUEVO 2] Display gestionado: se prende solo en eventos y se apaga solo.
bool          displayPrendido = false;
unsigned long displayApagaEn  = 0;   // millis() en que toca apagar

// [NUEVO 1] Cache de últimas mediciones propias del master
float ultTempMaster = NAN;
float ultHumMaster  = NAN;
unsigned long ultMedicionMaster = 0;

// [NUEVO v18.5] Umbrales para alertas propias del master. Deben coincidir
// con los del slave (UMBRALES en sens.py) para consistencia visual.
const float TEMP_WARN = 45.0;
const float TEMP_CRIT = 60.0;
const float TEMP_WARN_SALE = 42.0;
const float TEMP_CRIT_SALE = 55.0;
const float HUM_WARN_BAJO = 15.0;
const float HUM_CRIT_BAJO =  8.0;
const float HUM_WARN_BAJO_SALE = 18.0;
const float HUM_CRIT_BAJO_SALE = 11.0;

// Estado de alerta propia (lo que el master mismo está reportando)
String nivelAlertaMaster = "";   // "", "WARN", "CRIT"
String sensoresAlertaMaster = ""; // "Temp" o "Temp,Hum" etc.
unsigned long ultimaAlertaMasterTx = 0;
const unsigned long T_ALERT_REPEAT_MS = 10000;   // re-enviar cada 10s mientras dure

// [FIX 6] Dedup global de FBs por (mid, nodo) con TTL
struct FbProcesado {
  String clave;          // "mid|nodo"
  unsigned long ts;
};
const int MAX_PROCESADOS = 40;
const unsigned long TTL_PROCESADOS_MS = 30000;  // 30s
FbProcesado procesados[MAX_PROCESADOS];
int procesadosCount = 0;

volatile bool flagServer = false;
volatile bool flagMesh   = false;
volatile bool flagBoost  = false;   // [v18.9] ráfaga fuerte de WAVE

// ───────────────────────────────────────────────
//  COLORES
// ───────────────────────────────────────────────
#define COLOR_VERDE    0x07E0
#define COLOR_ROJO     0xF800
#define COLOR_AMARILLO 0xFFE0
#define COLOR_CYAN     0x07FF
#define COLOR_BLANCO   0xFFFF
#define COLOR_NEGRO    0x0000
#define COLOR_GRIS     0x528A

// ───────────────────────────────────────────────
//  FORWARD DECLARATIONS
// ───────────────────────────────────────────────
void onEspNowRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len);
void enviarWave(const char* cmd, const char* target, uint32_t mid);
void enviarBoost();
void ventanaMesh(const String& cmdParam, const String& targetParam);
bool consultarHttp();
void publicarMqtt();
bool conectarWifi();
bool initEspNow();
void uiHeartbeat();
void uiBienvenida();
void uiStatus(const char* l1, const char* l2, uint16_t c1, uint16_t c2);
void uiPantallaCompleta();

// ───────────────────────────────────────────────
//  HELPERS DE TIEMPO
// ───────────────────────────────────────────────
String horaLocal(bool conFecha = false) {
  time_t now = time(nullptr) + TZ_OFFSET_HRS * 3600;
  struct tm* lt = gmtime(&now);
  char buf[32];
  if (conFecha) {
    snprintf(buf, sizeof(buf), "%04d-%02d-%02d %02d:%02d:%02d",
             lt->tm_year + 1900, lt->tm_mon + 1, lt->tm_mday,
             lt->tm_hour, lt->tm_min, lt->tm_sec);
  } else {
    snprintf(buf, sizeof(buf), "%02d:%02d:%02d",
             lt->tm_hour, lt->tm_min, lt->tm_sec);
  }
  return String(buf);
}

void encolarSubida(const String& linea) {
  if (colaSubidaCount < MAX_COLA_SUBIDA) {
    colaSubida[colaSubidaCount++] = linea;
  } else {
    Serial.println("[WARN] cola subida llena, descartando");
  }
}

void encolarBajada(const String& json) {
  if (colaBajadaCount < MAX_COLA_BAJADA) {
    colaBajada[colaBajadaCount++] = json;
  }
}

uint32_t nextMsgId() { return ++msgCounter; }

// ───────────────────────────────────────────────
//  [NUEVO 2] CONTROL DE DISPLAY
//  Se prende centralizadamente con prenderDisplay() y se apaga
//  automáticamente desde el loop principal cuando vence el timeout.
// ───────────────────────────────────────────────
void prenderDisplay() {
  digitalWrite(PIN_BACKLIGHT, HIGH);
  displayPrendido = true;
  displayApagaEn  = millis() + T_DISPLAY_ON;
}

void pollDisplay() {
  if (displayPrendido && millis() > displayApagaEn) {
    digitalWrite(PIN_BACKLIGHT, LOW);
    displayPrendido = false;
  }
}

// ───────────────────────────────────────────────
//  [NUEVO 1] MEDICIÓN DHT11 PROPIA
//  Tres intentos con 300ms entre ellos. Devuelve true si OK.
//  El DHT11 necesita ~1s entre lecturas, así que cache ayuda.
// ───────────────────────────────────────────────
bool medirDht11Master() {
  // Si la última medición es muy reciente (<3s), reusar cache
  if (millis() - ultMedicionMaster < 3000 && !isnan(ultTempMaster)) {
    return true;
  }
  for (int i = 0; i < 3; i++) {
    float h = dhtMaster.readHumidity();
    float t = dhtMaster.readTemperature();
    if (!isnan(h) && !isnan(t)) {
      ultHumMaster  = h;
      ultTempMaster = t;
      ultMedicionMaster = millis();
      Serial.printf("[DHT11 MASTER] T:%.1f H:%.1f\n", t, h);
      return true;
    }
    delay(300);
  }
  Serial.println("[DHT11 MASTER] error de lectura");
  return false;
}

// ───────────────────────────────────────────────
//  [NUEVO 1] Encolar línea ALERT al servidor.
//  Formato: "ts,nodo,ALERT,NIVEL:Sensor1,Sensor2"
//  El servidor parsea esto aparte de las mediciones normales.
// ───────────────────────────────────────────────
void encolarAlerta(const String& ts, const String& nodo,
                   const String& nivel, const String& sensores) {
  String l = ts + "," + nodo + ",ALERT," + nivel + ":" + sensores;
  encolarSubida(l);
  Serial.printf("[ALERT TX] %s %s sensores:%s\n",
                nodo.c_str(), nivel.c_str(), sensores.c_str());
}

// ───────────────────────────────────────────────
//  [NUEVO 2] Evalúa alertas del DHT11 propio del master.
//  Usa histéresis igual que los slaves. Si hay alerta nueva o cambio
//  de nivel, encola ALERT al servidor. Mientras dure la alerta, re-
//  manda cada T_ALERT_REPEAT_MS para que el servidor no la "olvide".
// ───────────────────────────────────────────────
void evaluarAlertasMaster() {
  if (isnan(ultTempMaster) || isnan(ultHumMaster)) return;

  String nuevoNivel = "";
  String sensores = "";

  // Temperatura (con histéresis)
  if (ultTempMaster >= TEMP_CRIT) {
    nuevoNivel = "CRIT";
    sensores += "Temp";
  } else if (nivelAlertaMaster == "CRIT" && ultTempMaster >= TEMP_CRIT_SALE) {
    nuevoNivel = "CRIT";
    sensores += "Temp";
  } else if (ultTempMaster >= TEMP_WARN) {
    if (nuevoNivel != "CRIT") nuevoNivel = "WARN";
    if (sensores.indexOf("Temp") < 0) {
      if (sensores.length() > 0) sensores += ",";
      sensores += "Temp";
    }
  } else if (nivelAlertaMaster.length() > 0 && ultTempMaster >= TEMP_WARN_SALE) {
    if (nuevoNivel != "CRIT") nuevoNivel = "WARN";
    if (sensores.indexOf("Temp") < 0) {
      if (sensores.length() > 0) sensores += ",";
      sensores += "Temp";
    }
  }

  // Humedad baja (con histéresis)
  if (ultHumMaster <= HUM_CRIT_BAJO) {
    nuevoNivel = "CRIT";
    if (sensores.indexOf("Hum") < 0) {
      if (sensores.length() > 0) sensores += ",";
      sensores += "Hum";
    }
  } else if (ultHumMaster <= HUM_WARN_BAJO) {
    if (nuevoNivel != "CRIT") nuevoNivel = "WARN";
    if (sensores.indexOf("Hum") < 0) {
      if (sensores.length() > 0) sensores += ",";
      sensores += "Hum";
    }
  }

  String ts = horaLocal(true);
  bool cambio = (nuevoNivel != nivelAlertaMaster);
  bool tocaRepetir = (nuevoNivel.length() > 0 &&
                      millis() - ultimaAlertaMasterTx > T_ALERT_REPEAT_MS);

  if (cambio || tocaRepetir) {
    if (nuevoNivel.length() > 0) {
      encolarAlerta(ts, CLIENT_ID, nuevoNivel, sensores);
      ultimaAlertaMasterTx = millis();
    } else if (nivelAlertaMaster.length() > 0) {
      encolarAlerta(ts, CLIENT_ID, "OK", "-");
      Serial.println("[ALERT] master salió de alerta");
    }
  }
  nivelAlertaMaster = nuevoNivel;
  sensoresAlertaMaster = sensores;
}

// ───────────────────────────────────────────────
//  [v18.6] Encolar PULSE — heartbeat sin medición.
//  El servidor sabe que el master sigue vivo, pero NO se guarda
//  como una medición en la tabla (el servidor lo filtra).
// ───────────────────────────────────────────────
void encolarPulse(const String& ts) {
  String l = ts + "," + CLIENT_ID + ",PULSE,ok";
  encolarSubida(l);
}

// ───────────────────────────────────────────────
//  Encolar medición del master + evaluar alertas
// ───────────────────────────────────────────────
void encolarMedicionMaster(const String& ts) {
  String l;
  if (medirDht11Master()) {
    char tBuf[8], hBuf[8];
    dtostrf(ultTempMaster, 0, 1, tBuf);
    dtostrf(ultHumMaster,  0, 1, hBuf);
    l = ts + "," + CLIENT_ID + ",T:" + tBuf + " H:" + hBuf + ",sensor";
    // [NUEVO 2] Evaluar alertas propias tras cada medición exitosa
    evaluarAlertasMaster();
  } else {
    l = ts + "," + CLIENT_ID + ",T:-- H:--,sensor";
  }
  encolarSubida(l);
}

// [FIX 6] Limpia dedup viejos por TTL
void limpiarProcesadosViejos() {
  unsigned long ahora = millis();
  int j = 0;
  for (int i = 0; i < procesadosCount; i++) {
    if (ahora - procesados[i].ts < TTL_PROCESADOS_MS) {
      if (j != i) procesados[j] = procesados[i];
      j++;
    }
  }
  procesadosCount = j;
}

bool yaProcesado(const String& clave) {
  for (int i = 0; i < procesadosCount; i++) {
    if (procesados[i].clave == clave) return true;
  }
  return false;
}

void marcarProcesado(const String& clave) {
  if (procesadosCount >= MAX_PROCESADOS) {
    // Tirar el más viejo
    int idx = 0;
    for (int i = 1; i < procesadosCount; i++) {
      if (procesados[i].ts < procesados[idx].ts) idx = i;
    }
    for (int i = idx; i < procesadosCount - 1; i++) {
      procesados[i] = procesados[i + 1];
    }
    procesadosCount--;
  }
  procesados[procesadosCount].clave = clave;
  procesados[procesadosCount].ts = millis();
  procesadosCount++;
}

void registrarNodo(const String& id, const String& par, JsonArray via) {
  int idx = -1;
  for (int i = 0; i < nodosCount; i++) {
    if (nodosVistos[i].id == id) { idx = i; break; }
  }
  if (idx == -1) {
    if (nodosCount >= MAX_NODOS) {
      idx = 0;
      for (int i = 1; i < nodosCount; i++) {
        if (nodosVistos[i].ultimoVisto < nodosVistos[idx].ultimoVisto) idx = i;
      }
    } else {
      idx = nodosCount++;
    }
    nodosVistos[idx].id = id;
    nodosVistos[idx].count = 0;
  }
  nodosVistos[idx].parent = par;
  nodosVistos[idx].ultimoVisto = millis();
  nodosVistos[idx].count++;
  if (!via || via.size() == 0) {
    nodosVistos[idx].via = "directo";
  } else {
    String v = "via_";
    for (size_t i = 0; i < via.size(); i++) {
      if (i > 0) v += ",";
      v += via[i].as<String>();
    }
    nodosVistos[idx].via = v;
  }
}

void encolarStatus(const String& ts) {
  String linea = ts + "," + CLIENT_ID + ",STATUS,";
  if (nodosCount == 0) {
    linea += "(sin nodos)";
  } else {
    for (int i = 0; i < nodosCount; i++) {
      if (i > 0) linea += "|";
      unsigned long edad = (millis() - nodosVistos[i].ultimoVisto) / 1000;
      linea += nodosVistos[i].id + ":" + nodosVistos[i].via +
               ":" + String(edad) + "s";
    }
  }
  encolarSubida(linea);
}

// ───────────────────────────────────────────────
//  ESP-NOW callback (corre en task del core 0,
//  asíncrono respecto al loop principal)
// ───────────────────────────────────────────────
void onEspNowRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  if (len <= 0 || len > 250) return;

  char buf[251];
  memcpy(buf, data, len);
  buf[len] = '\0';

  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, buf);
  if (err) {
    Serial.print("[RX ERR JSON] ");
    Serial.println(err.c_str());
    return;
  }

  const char* tipo = doc["type"];
  if (!tipo) return;

  // [v18.7] Filtro de red: ignorar paquetes que no sean de nuestra malla.
  // Esto evita que el master procese FBs de otra persona usando ESP-NOW cerca.
  const char* net = doc["net"] | (const char*)nullptr;
  if (!net || strcmp(net, NET_ID) != 0) {
    // Paquete de otra red (o sin net) — ignorar silenciosamente
    return;
  }

  if (strcmp(tipo, "FB") == 0 || strcmp(tipo, "FEEDBACK") == 0) {
    String nodo = doc["id"] | "?";
    String par  = doc["par"] | "?";
    uint32_t mid = doc["mid"] | 0;

    // [FIX 6] Dedup por (mid, nodo) global con TTL
    String clave = String(mid) + "|" + nodo;
    if (yaProcesado(clave)) {
      Serial.printf("[DEDUP] FB %s mid:%lu ya procesado\n",
                    nodo.c_str(), (unsigned long)mid);
      return;
    }
    marcarProcesado(clave);

    JsonArray via = doc["via"].as<JsonArray>();
    String ts = horaLocal(true);

    JsonArray pl = doc["pl"].as<JsonArray>();
    if (!pl) pl = doc["payload"].as<JsonArray>();

    String tVal = "", hVal = "";
    String otrosCSV = "";
    if (pl) {
      for (JsonObject m : pl) {
        const char* t = m["t"] | (const char*)(m["tipo"] | "?");
        String v;
        if (m["v"].is<int>())         v = String(m["v"].as<int>());
        else if (m["v"].is<float>())  v = String(m["v"].as<float>(), 2);
        else                          v = String(m["v"].as<const char*>() ?
                                                  m["v"].as<const char*>() : "?");

        // [FIX 5b] Si la medición trae su propio "ts", úsalo
        const char* tsMed = m["ts"] | (const char*)nullptr;
        String tsLinea = tsMed ? String(tsMed) : ts;

        if (strcmp(t, "Temp") == 0 || strcmp(t, "Temperatura") == 0)      tVal = v;
        else if (strcmp(t, "Hum") == 0 || strcmp(t, "Humedad") == 0)      hVal = v;
        else otrosCSV += tsLinea + "," + nodo + "," + t + "," + v + "\n";
      }
    }

    if (tVal.length() > 0 || hVal.length() > 0) {
      String l = ts + "," + nodo + ",T:" + (tVal.length() > 0 ? tVal : "?") +
                 " H:" + (hVal.length() > 0 ? hVal : "?") + ",sensor";
      encolarSubida(l);
    }
    int start = 0;
    while (start < (int)otrosCSV.length()) {
      int nl = otrosCSV.indexOf('\n', start);
      if (nl < 0) break;
      encolarSubida(otrosCSV.substring(start, nl));
      start = nl + 1;
    }

    registrarNodo(nodo, par, via);

    String ruta = (via && via.size() > 0) ? "via " : "directo";
    if (via) {
      for (size_t i = 0; i < via.size(); i++) {
        if (i > 0) ruta += ",";
        ruta += via[i].as<String>();
      }
    }
    Serial.printf("[<<< FB RX] nodo:%s par:%s mid:%lu ruta:%s\n",
                  nodo.c_str(), par.c_str(), (unsigned long)mid, ruta.c_str());

    // [v18.5] Detectar alerta en el FB y propagar al servidor.
    // El campo "alert" es ahora un string: "WARN" o "CRIT" (antes era bool).
    const char* nivel = doc["alert"] | (const char*)nullptr;
    if (nivel) {
      JsonArray aT = doc["a_t"].as<JsonArray>();
      String sensores = "";
      if (aT) {
        for (size_t i = 0; i < aT.size(); i++) {
          if (i > 0) sensores += ",";
          sensores += aT[i].as<String>();
        }
      } else {
        sensores = "?";
      }
      encolarAlerta(ts, nodo, String(nivel), sensores);
    }

    // [NUEVO 2] FB recibido → prender display
    prenderDisplay();
  }
}

// ───────────────────────────────────────────────
//  ESP-NOW init
// ───────────────────────────────────────────────
bool initEspNow() {
  if (esp_now_init() != ESP_OK) {
    Serial.println("[ESPNOW] init fallo");
    return false;
  }
  esp_now_register_recv_cb(onEspNowRecv);

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST_MAC, 6);
  peer.channel = canalActual;   // [FIX 2] explícito, no 0
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) {
    Serial.println("[ESPNOW] add_peer fallo");
    return false;
  }
  Serial.printf("[ESPNOW] OK peer broadcast registrado en ch:%d\n", canalActual);
  return true;
}

// ───────────────────────────────────────────────
//  WiFi
// ───────────────────────────────────────────────
bool conectarWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    wifiOk = true;
    return true;
  }
  // [FIX 4] Solo cambiar modo si no está ya en STA
  if (WiFi.getMode() != WIFI_STA) {
    WiFi.mode(WIFI_STA);
  }

  // [v18.9] Probar cada red conocida en orden. Si ya teníamos una red
  // enganchada (redActiva>=0) la probamos primero para reconectar rápido.
  for (int n = 0; n < N_REDES; n++) {
    int idx = (redActiva >= 0) ? (redActiva + n) % N_REDES : n;
    RedWiFi& r = REDES[idx];

    Serial.printf("[WIFI] Probando red %d/%d: %s ...\n", n + 1, N_REDES, r.ssid);
    WiFi.begin(r.ssid, r.pass);

    unsigned long inicio = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - inicio < 8000) {
      delay(200);
      Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
      // [FIX 1] CRÍTICO: apagar modem-sleep para coexistencia con ESP-NOW
      WiFi.setSleep(false);
      esp_wifi_set_ps(WIFI_PS_NONE);
      // [v18.9] Potencia TX al máximo: más alcance hacia los slaves.
      WiFi.setTxPower(WIFI_POWER_19_5dBm);

      // [v18.9] Fijar la config ACTIVA según la red que enganchó.
      bool cambioRed  = (redActiva != idx);
      redActiva   = idx;
      SERVER_IP   = r.server_ip;
      SERVER_PORT = r.server_port;
      MQTT_BROKER = r.mqtt_broker;

      wifiOk = true;
      canalActual = WiFi.channel();
      // [FIX 2] forzar canal de ESP-NOW al del AP
      esp_wifi_set_channel(canalActual, WIFI_SECOND_CHAN_NONE);

      // [v18.9] Reapuntar MQTT al broker de ESTA red. Si cambiamos de red,
      // cerramos la sesión previa para que reconecte al broker nuevo.
      mqttClient.setServer(MQTT_BROKER.c_str(), MQTT_PORT);
      if (cambioRed && mqttClient.connected()) mqttClient.disconnect();

      Serial.printf("[WIFI] OK red:%s ip:%s ch:%d srv:%s (modem-sleep OFF, TX max)\n",
                    r.ssid, WiFi.localIP().toString().c_str(),
                    canalActual, SERVER_IP.c_str());
      return true;
    }
    Serial.printf("[WIFI] %s no respondió, siguiente...\n", r.ssid);
  }

  wifiOk = false;
  Serial.println("[WIFI] Ninguna red conocida disponible");
  return false;
}

// ───────────────────────────────────────────────
//  HTTP polling de comandos del servidor
// ───────────────────────────────────────────────
bool consultarHttp() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTP] WiFi no conectado, skip");
    return false;
  }

  HTTPClient http;
  String url = String("http://") + SERVER_IP + ":" + SERVER_PORT + "/comandos";
  http.begin(url);
  http.setTimeout(2000);
  int code = http.GET();

  if (code != 200) {
    Serial.printf("[HTTP GET] code:%d url:%s\n", code, url.c_str());
    http.end();
    return false;
  }

  String body = http.getString();
  http.end();

  StaticJsonDocument<512> doc;
  DeserializationError jerr = deserializeJson(doc, body);
  if (jerr) {
    Serial.printf("[HTTP JSON ERR] %s body:%s\n", jerr.c_str(), body.c_str());
    return false;
  }

  JsonArray cmds = doc["comandos"].as<JsonArray>();
  if (!cmds || cmds.size() == 0) {
    static unsigned long ultimoLog = 0;
    if (millis() - ultimoLog > 30000) {
      ultimoLog = millis();
      Serial.println("[HTTP] poll OK, sin comandos pendientes");
    }
    return false;
  }

  Serial.printf("[<<< HTTP RX] %d comandos\n", cmds.size());

  int n = 0;
  for (JsonVariant cmd : cmds) {
    String txt = cmd.as<String>();
    Serial.printf("  cmd[%d]: %s\n", n, txt.c_str());

    if (txt == "PAIR") {
      String ts = horaLocal(true);
      encolarSubida(ts + "," + CLIENT_ID + ",T:-- H:--,sensor");
      encolarStatus(ts);
    } else {
      uint32_t mid = nextMsgId();
      String target = "ALL";
      if (txt.startsWith("REQ:") && txt != "REQ:ALL") target = txt.substring(4);

      StaticJsonDocument<320> wave;
      wave["type"]   = "WAVE";
      wave["net"]    = NET_ID;             // [v18.7]
      wave["cmd"]    = txt;
      wave["from"]   = CLIENT_ID;
      wave["target"] = target;
      wave["ttl"]    = 6;
      wave["ch"]     = canalActual;
      wave["mid"]    = mid;
      wave["ts"]     = horaLocal(true);   // [FIX 5] hora propagada
      String waveStr;
      serializeJson(wave, waveStr);
      encolarBajada(waveStr);
    }
    n++;
  }

  HTTPClient ack;
  String ackUrl = String("http://") + SERVER_IP + ":" + SERVER_PORT + "/comandos/ack";
  ack.begin(ackUrl);
  ack.addHeader("Content-Type", "application/json");
  ack.setTimeout(2000);
  String ackBody = "{\"n\":" + String(n) + "}";
  int ackCode = ack.POST(ackBody);
  ack.end();
  if (ackCode != 200) Serial.printf("[HTTP ACK] code:%d\n", ackCode);

  return true;
}

// ───────────────────────────────────────────────
//  MQTT publicar datos
// ───────────────────────────────────────────────
void publicarMqtt() {
  if (colaSubidaCount == 0) return;
  if (WiFi.status() != WL_CONNECTED) return;

  if (!mqttClient.connected()) {
    mqttClient.setServer(MQTT_BROKER.c_str(), MQTT_PORT);
    if (!mqttClient.connect(CLIENT_ID)) {
      Serial.printf("[MQTT] connect fallo state:%d\n", mqttClient.state());
      mqttOk = false;
      return;
    }
  }
  mqttOk = true;

  int enviados = 0;
  for (int i = 0; i < colaSubidaCount; i++) {
    bool ok = mqttClient.publish(TOPIC_PUB, colaSubida[i].c_str());
    if (ok) {
      enviados++;
      Serial.printf("[MQTT TX] %s\n", colaSubida[i].c_str());
    } else {
      Serial.printf("[MQTT FAIL] %s\n", colaSubida[i].c_str());
      break;
    }
  }
  if (enviados > 0) {
    for (int i = enviados; i < colaSubidaCount; i++) {
      colaSubida[i - enviados] = colaSubida[i];
    }
    colaSubidaCount -= enviados;
  }
  Serial.printf("[MQTT OK] enviados:%d restantes:%d\n", enviados, colaSubidaCount);
}

// ───────────────────────────────────────────────
//  ENVIAR WAVE (mesh)
// ───────────────────────────────────────────────
void enviarWave(const char* cmd, const char* target, uint32_t mid) {
  StaticJsonDocument<320> doc;
  doc["type"]   = "WAVE";
  doc["net"]    = NET_ID;             // [v18.7]
  doc["cmd"]    = cmd;
  doc["from"]   = CLIENT_ID;
  doc["target"] = target;
  doc["ttl"]    = 6;
  doc["ch"]     = canalActual;
  doc["mid"]    = mid;
  doc["ts"]     = horaLocal(true);   // [FIX 5]
  String s;
  serializeJson(doc, s);

  Serial.printf("[>>> MESH TX] mid:%lu cmd:%s target:%s\n",
                (unsigned long)mid, cmd, target);

  int exitos = 0;
  for (int i = 0; i < BROADCAST_N; i++) {
    esp_err_t r = esp_now_send(BROADCAST_MAC, (uint8_t*)s.c_str(), s.length());
    if (r == ESP_OK) exitos++;
    delay(150);
  }
  Serial.printf("[TX DONE] exitos:%d/%d\n", exitos, BROADCAST_N);
}

// ───────────────────────────────────────────────
//  [v18.9] BOOST — ráfaga fuerte de WAVE (pulsación larga botón izq)
//  Sube TX al máximo y bombardea REQ:ALL durante ~3s. Un slave que esté
//  barriendo canales (1.2s por canal) cae en varias de estas WAVE y
//  engancha al master de inmediato. Útil para recuperar slaves que se
//  quedaron sincronizados entre sí en otro canal (malla huérfana).
// ───────────────────────────────────────────────
void enviarBoost() {
  // Potencia máxima por si el stack la bajó; 84 = 21 dBm en pasos de 0.25.
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  esp_wifi_set_max_tx_power(84);

  uint32_t mid = nextMsgId();
  StaticJsonDocument<320> doc;
  doc["type"]   = "WAVE";
  doc["net"]    = NET_ID;
  doc["cmd"]    = "REQ:ALL";
  doc["from"]   = CLIENT_ID;
  doc["target"] = "ALL";
  doc["ttl"]    = 6;
  doc["ch"]     = canalActual;
  doc["mid"]    = mid;
  doc["ts"]     = horaLocal(true);
  String s;
  serializeJson(doc, s);

  Serial.printf("[BOOST] ráfaga fuerte mid:%lu ch:%d reps:%d\n",
                (unsigned long)mid, canalActual, BOOST_REPS);

  int exitos = 0;
  for (int i = 0; i < BOOST_REPS; i++) {
    esp_err_t r = esp_now_send(BROADCAST_MAC, (uint8_t*)s.c_str(), s.length());
    if (r == ESP_OK) exitos++;
    // Mantener MQTT vivo durante la ráfaga
    if (mqttClient.connected()) mqttClient.loop();
    delay(BOOST_GAP_MS);
  }
  Serial.printf("[BOOST DONE] exitos:%d/%d\n", exitos, BOOST_REPS);
}
//  No genera respuestas (target="NONE") pero permite a los slaves
//  encontrar el canal y actualizar su RTC.
// ───────────────────────────────────────────────
void enviarBeacon() {
  StaticJsonDocument<256> doc;
  doc["type"]   = "WAVE";
  doc["net"]    = NET_ID;             // [v18.7]
  doc["cmd"]    = "BEACON";
  doc["from"]   = CLIENT_ID;
  doc["target"] = "NONE";   // ningún slave responde
  doc["ttl"]    = 3;        // TTL bajo: alcanza ~2 saltos, no satura
  doc["ch"]     = canalActual;
  doc["mid"]    = nextMsgId();
  doc["ts"]     = horaLocal(true);
  doc["h"]      = 0;            // [v19.0] distancia del master = 0 (para hops del slave)
  String s;
  serializeJson(doc, s);

  // Solo 1 transmisión — es informativo, no crítico
  esp_now_send(BROADCAST_MAC, (uint8_t*)s.c_str(), s.length());
  // [v18.8] Con BEACON cada 3s, solo logueamos 1 de cada 10 para no saturar
  static int beaconLogCount = 0;
  if (++beaconLogCount >= 10) {
    beaconLogCount = 0;
    Serial.printf("[BEACON TX] ch:%d ts:%s (cada 3s)\n",
                  canalActual, horaLocal(false).c_str());
  }
}

// ───────────────────────────────────────────────
//  VENTANA MESH — enviar y escuchar FBs
// ───────────────────────────────────────────────
void ventanaMesh(const String& cmdParam, const String& targetParam) {
  if (colaBajadaCount > 0) {
    String onda = colaBajada[0];
    for (int i = 1; i < colaBajadaCount; i++) colaBajada[i-1] = colaBajada[i];
    colaBajadaCount--;

    StaticJsonDocument<320> doc;
    deserializeJson(doc, onda);
    const char* c = doc["cmd"] | "REQ:ALL";
    uint32_t mid  = doc["mid"] | 0;

    Serial.printf("[>>> MESH TX servidor] mid:%lu cmd:%s\n",
                  (unsigned long)mid, c);

    int exitos = 0;
    for (int i = 0; i < BROADCAST_N; i++) {
      esp_err_t r = esp_now_send(BROADCAST_MAC, (uint8_t*)onda.c_str(), onda.length());
      if (r == ESP_OK) exitos++;
      delay(150);
    }
    Serial.printf("[TX DONE] exitos:%d/%d\n", exitos, BROADCAST_N);
  } else {
    enviarWave(cmdParam.c_str(), targetParam.c_str(), nextMsgId());
  }

  // Escuchar FBs durante T_MESH_LISTEN.
  // El callback onEspNowRecv corre asíncronamente; aquí solo cedemos CPU
  // y mantenemos servicios vivos (MQTT keepalive).
  unsigned long fin = millis() + T_MESH_LISTEN;
  while (millis() < fin) {
    if (mqttClient.connected()) mqttClient.loop();
    delay(10);
  }
  Serial.printf("[MESH OK] nodos_total:%d procesados_buf:%d\n",
                nodosCount, procesadosCount);
}

// ───────────────────────────────────────────────
//  UI
// ───────────────────────────────────────────────
void uiBienvenida() {
  tft.fillScreen(COLOR_NEGRO);
  tft.setTextColor(COLOR_VERDE, COLOR_NEGRO);
  tft.setTextSize(3);
  tft.setCursor(10, 20);
  tft.println("PIF MASTER");
  tft.setTextColor(COLOR_CYAN, COLOR_NEGRO);
  tft.setTextSize(2);
  tft.setCursor(10, 60);
  tft.println("LAB-ARTE");
  tft.setTextColor(COLOR_AMARILLO, COLOR_NEGRO);
  tft.setCursor(10, 90);
  tft.println("v19.0 / C++");
  prenderDisplay();
  delay(2000);
}

void uiStatus(const char* l1, const char* l2, uint16_t c1, uint16_t c2) {
  tft.fillScreen(COLOR_NEGRO);
  tft.setTextSize(2);
  tft.setTextColor(COLOR_VERDE, COLOR_NEGRO);
  tft.setCursor(4, 4);
  tft.print("PIF MASTER v18.8");
  tft.setTextColor(c1, COLOR_NEGRO);
  tft.setCursor(4, 30);
  tft.print(l1);
  if (l2) {
    tft.setTextColor(c2, COLOR_NEGRO);
    tft.setCursor(4, 60);
    tft.print(l2);
  }
  // [NUEVO 2] el backlight lo controla prenderDisplay/pollDisplay
}

// [NUEVO 2] El heartbeat YA NO toca la pantalla. Solo dibuja si el
// display está prendido por otra causa.
void uiHeartbeat() {
  if (!displayPrendido) return;   // sin display, no malgastar SPI

  tft.fillScreen(COLOR_NEGRO);
  tft.setTextSize(2);
  tft.setTextColor(COLOR_VERDE, COLOR_NEGRO);
  tft.setCursor(4, 4);
  tft.print("Master v18.8");

  tft.setTextSize(1);
  tft.setTextColor(COLOR_GRIS, COLOR_NEGRO);
  tft.setCursor(180, 8);
  tft.print(horaLocal(false));

  tft.setTextSize(2);
  tft.setTextColor(COLOR_AMARILLO, COLOR_NEGRO);
  tft.setCursor(4, 30);
  tft.printf("Nodos: %d", nodosCount);

  // [NUEVO 1] mostrar T/H propias del master
  tft.setTextColor(COLOR_CYAN, COLOR_NEGRO);
  tft.setCursor(4, 55);
  if (!isnan(ultTempMaster)) {
    tft.printf("T:%.1fC H:%.0f%%", ultTempMaster, ultHumMaster);
  } else {
    tft.printf("ch:%d wifi:%s", canalActual, wifiOk ? "OK" : "--");
  }

  tft.setTextColor(COLOR_GRIS, COLOR_NEGRO);
  tft.setCursor(4, 85);
  tft.printf("cola_sub:%d mqtt:%s", colaSubidaCount, mqttOk ? "OK" : "--");
}

void uiPantallaCompleta() {
  tft.fillScreen(COLOR_NEGRO);
  tft.setTextSize(2);
  tft.setTextColor(COLOR_VERDE, COLOR_NEGRO);
  tft.setCursor(4, 4);
  tft.print("PIF MASTER");

  tft.setTextSize(1);
  tft.setTextColor(COLOR_CYAN, COLOR_NEGRO);
  tft.setCursor(180, 10);
  tft.print(horaLocal(false));

  // [NUEVO 1] Mediciones propias arriba
  tft.setTextSize(2);
  if (!isnan(ultTempMaster)) {
    tft.setTextColor(COLOR_AMARILLO, COLOR_NEGRO);
    tft.setCursor(4, 30);
    tft.printf("T:%.1fC H:%.0f%%", ultTempMaster, ultHumMaster);
  }

  tft.setTextSize(1);
  tft.setTextColor(COLOR_GRIS, COLOR_NEGRO);
  tft.setCursor(4, 58);
  tft.printf("Nodos: %d  ch:%d", nodosCount, canalActual);

  for (int i = 0; i < nodosCount && i < 3; i++) {
    tft.setCursor(4, 75 + i * 14);
    tft.printf("%s %s", nodosVistos[i].id.c_str(), nodosVistos[i].via.c_str());
  }
  // [NUEVO 2] el backlight lo controla prenderDisplay/pollDisplay
}

// ───────────────────────────────────────────────
//  BOTONES
// ───────────────────────────────────────────────
void pollBotones() {
  static unsigned long ultimoRight = 0;
  // [v18.9] Estado de la pulsación del botón izq para distinguir corta/larga.
  static unsigned long leftDown   = 0;      // ms en que se presionó (0 = suelto)
  static bool          boostFired = false;  // ya disparó boost en esta pulsación
  unsigned long ahora = millis();

  // ── Botón IZQUIERDO: corta = WAVE manual, larga (>=1.5s) = BOOST ──
  bool leftPress = (digitalRead(PIN_BTN_LEFT) == LOW);
  if (leftPress) {
    if (leftDown == 0) leftDown = ahora;              // flanco de bajada
    // Mientras se mantiene: al cruzar el umbral, dispara boost una sola vez
    if (!boostFired && ahora - leftDown >= T_BOOST_HOLD_MS) {
      boostFired = true;
      flagBoost  = true;
    }
  } else {
    if (leftDown != 0) {                               // flanco de subida (soltó)
      unsigned long held = ahora - leftDown;
      if (!boostFired && held > 50) flagMesh = true;   // fue pulsación corta
      leftDown   = 0;
      boostFired = false;
    }
  }

  // ── Botón DERECHO: medir + publicar (igual que antes) ──
  if (digitalRead(PIN_BTN_RIGHT) == LOW && ahora - ultimoRight > 500) {
    ultimoRight = ahora;
    flagServer = true;
  }
}

// ───────────────────────────────────────────────
//  SETUP
// ───────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== PIF MASTER v18.8 / Arduino C++ ===");

  pinMode(PIN_BACKLIGHT, OUTPUT);
  pinMode(PIN_BTN_LEFT,  INPUT_PULLUP);
  pinMode(PIN_BTN_RIGHT, INPUT_PULLUP);

  // [NUEVO 1] Iniciar sensor DHT11 propio
  dhtMaster.begin();
  Serial.printf("[DHT11] iniciado en pin %d\n", PIN_DHT11);

  tft.init();
  tft.setRotation(1);
  uiBienvenida();

  // 1) WiFi primero (esto deja modem-sleep OFF y fija canal)
  if (!conectarWifi()) {
    uiStatus("WiFi ERR", "Reintentando...", COLOR_ROJO, COLOR_AMARILLO);
    delay(3000);
    conectarWifi();   // un retry
  }

  // 2) ESP-NOW (encima del STA, en el canal del AP)
  if (!initEspNow()) {
    uiStatus("ESPNOW ERR", "Reset fisico", COLOR_ROJO, COLOR_AMARILLO);
    while (true) delay(5000);
  }

  // 3) MQTT lazy connect
  mqttClient.setServer(MQTT_BROKER.c_str(), MQTT_PORT);
  mqttClient.setKeepAlive(45);
  mqttClient.setSocketTimeout(5);

  // 4) NTP
  configTime(0, 0, "pool.ntp.org");
  Serial.println("[NTP] solicitado");

  uiStatus("Listo", "PIF Mesh activo", COLOR_VERDE, COLOR_GRIS);
  delay(1500);
  // [v18.6] Una medición inicial para que la tarjeta del master arranque con datos
  // en lugar de "T:-- H:--". Si falla, queda NaN y se reintentará en el primer check.
  Serial.println("[SETUP] medición inicial DHT11...");
  delay(2000);  // DHT11 necesita ~1s tras boot para estabilizarse
  if (medirDht11Master()) {
    String ts = horaLocal(true);
    encolarMedicionMaster(ts);
    publicarMqtt();
    ultimaMedicionPropia = millis();
  }
  Serial.println("[OK] Master listo, entrando al loop\n");
}

// ───────────────────────────────────────────────
//  LOOP
// ───────────────────────────────────────────────
void loop() {
  pollBotones();
  pollDisplay();                       // [NUEVO 2] apaga display si toca

  // [FIX 3] CRÍTICO: mantener vivo el MQTT keepalive
  if (mqttClient.connected()) {
    mqttClient.loop();
  }

  // Limpieza periódica del dedup (barata, cada loop)
  static unsigned long ultimaLimpieza = 0;
  if (millis() - ultimaLimpieza > 5000) {
    ultimaLimpieza = millis();
    limpiarProcesadosViejos();
  }

  // ── Botón derecho: medir + publicar ──
  if (flagServer) {
    flagServer = false;
    prenderDisplay();                  // [NUEVO 2]
    uiStatus("Servidor", "publicando...", COLOR_AMARILLO, COLOR_GRIS);
    String ts = horaLocal(true);
    encolarMedicionMaster(ts);         // [NUEVO 1] medir DHT11 propio
    encolarStatus(ts);
    if (WiFi.status() != WL_CONNECTED) conectarWifi();
    publicarMqtt();
    uiPantallaCompleta();
  }

  // ── Botón izquierdo: WAVE manual ──
  if (flagMesh) {
    flagMesh = false;
    prenderDisplay();                  // [NUEVO 2]
    uiStatus("Malla", "WAVE manual...", COLOR_AMARILLO, COLOR_GRIS);
    ventanaMesh("REQ:ALL", "ALL");
    if (colaSubidaCount > 0) publicarMqtt();
  }

  // ── Botón izquierdo (pulsación larga): BOOST de WAVE ──
  if (flagBoost) {
    flagBoost = false;
    prenderDisplay();
    uiStatus("BOOST", "WAVE fuerte!", COLOR_CYAN, COLOR_VERDE);
    enviarBoost();
    // Tras la ráfaga, escuchar respuestas de los slaves que engancharon
    ventanaMesh("REQ:ALL", "ALL");
    if (colaSubidaCount > 0) publicarMqtt();
  }

  // ── HTTP polling cada T_HTTP_POLL ──
  if (millis() - ultimoHttpPoll > T_HTTP_POLL) {
    ultimoHttpPoll = millis();
    if (WiFi.status() != WL_CONNECTED) conectarWifi();
    bool huboCmd = consultarHttp();
    if (huboCmd && colaBajadaCount > 0) {
      prenderDisplay();                // [NUEVO 2] comando del servidor
      uiStatus("Comando RX", "ejecutando...", COLOR_CYAN, COLOR_AMARILLO);
      ventanaMesh("REQ:ALL", "ALL");
      if (colaSubidaCount > 0) publicarMqtt();
    }
    if (colaSubidaCount > 0) publicarMqtt();
  }

  // WAVE-beacon cada T_BEACON ms
  if (millis() - ultimoBeacon > T_BEACON) {
    ultimoBeacon = millis();
    enviarBeacon();
  }

  // [v18.6] Heartbeat MQTT cada T_HB_MQTT — solo PULSE + STATUS, SIN medición.
  // Mantiene al servidor sabiendo que el master sigue vivo y le da topología.
  if (millis() - ultimoHbMqtt > T_HB_MQTT) {
    ultimoHbMqtt = millis();
    if (WiFi.status() == WL_CONNECTED) {
      String ts = horaLocal(true);
      encolarPulse(ts);                // [v18.6] heartbeat sin medir
      encolarStatus(ts);
      publicarMqtt();
    }
  }

  // [v18.6] Medición propia del DHT11 cada T_MEDICION_PROPIA (10 min).
  // Mismo ritmo que los slaves. Si hay alerta activa, evaluarAlertasMaster()
  // ya manda con frecuencia alta (~10s) por su cuenta dentro de encolarMedicionMaster.
  if (millis() - ultimaMedicionPropia > T_MEDICION_PROPIA) {
    ultimaMedicionPropia = millis();
    if (WiFi.status() == WL_CONNECTED) {
      String ts = horaLocal(true);
      encolarMedicionMaster(ts);
      publicarMqtt();
    }
  }

  // [v18.6] Check rápido de alertas cada 30s, SIN publicar medición.
  // Solo lee el DHT11 y llama a evaluarAlertasMaster(), que internamente
  // decide si encola una línea ALERT (al cambiar nivel o cada T_ALERT_REPEAT_MS).
  // Esto permite detectar fuego sin esperar 10 minutos a la próxima medición.
  if (millis() - ultimoCheckAlerta > T_CHECK_ALERTA) {
    ultimoCheckAlerta = millis();
    if (medirDht11Master()) {       // actualiza ultTempMaster/ultHumMaster
      evaluarAlertasMaster();       // encola ALERT si aplica
    }
    if (colaSubidaCount > 0) publicarMqtt();
  }

  // ── Heartbeat (solo serial; UI solo si display ya está prendido) ──
  if (millis() - ultimoHeartbeat > T_HEARTBEAT) {
    ultimoHeartbeat = millis();
    uiHeartbeat();
    Serial.printf("[HEARTBEAT] nodos:%d cola_sub:%d cola_baj:%d wifi:%s mqtt:%s disp:%s\n",
                  nodosCount, colaSubidaCount, colaBajadaCount,
                  WiFi.status() == WL_CONNECTED ? "OK" : "--",
                  mqttClient.connected() ? "OK" : "--",
                  displayPrendido ? "ON" : "off");
  }

  // ── Pantalla completa cada 10 min — solo refresca STATUS, no fuerza display ──
  if (millis() - ultimaPantalla > T_PANTALLA) {
    ultimaPantalla = millis();
    String ts = horaLocal(true);
    encolarStatus(ts);
    if (displayPrendido) uiPantallaCompleta();
  }

  delay(10);
}
