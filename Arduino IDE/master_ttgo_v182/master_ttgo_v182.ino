// ============================================================
//  MASTER_TTGO v18.2 — PIF Mesh / LAB-ARTE
//  Arduino C++ port — TTGO T-Display
//
//  Cambios vs v18.1:
//    [FIX 1] WiFi.setSleep(false) + esp_wifi_set_ps(WIFI_PS_NONE)
//            → sin esto, el modem-sleep del WiFi tira paquetes ESP-NOW
//              silenciosamente. Era la causa #1 de "no veo nodos".
//    [FIX 2] peer.channel = canalActual (no 0) + esp_wifi_set_channel
//            → fuerza el canal de ESP-NOW al del AP de forma explícita.
//    [FIX 3] mqttClient.loop() en cada iteración del loop principal
//            → sin esto, el broker tira la conexión por keepalive y
//              los siguientes publish fallan. Era la causa de "no se
//              conecta bien al servidor".
//    [FIX 4] WiFi.mode(WIFI_STA) solo cuando no está ya en STA
//            → evita resetear el driver y derribar ESP-NOW.
//    [FIX 5] Campo "ts" agregado al WAVE — el slave sincroniza su RTC
//            con la hora del master.
//    [FIX 6] Dedup global de mids procesados por (mid,id) con TTL,
//            no solo por nodo dentro de la ventana.
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

// ───────────────────────────────────────────────
//  CONFIGURACIÓN — ajustar según red activa
// ───────────────────────────────────────────────
const char* WIFI_SSID   = "LIIM";
const char* WIFI_PASS   = "93001045";
const char* SERVER_IP   = "192.168.0.109";
const int   SERVER_PORT = 5000;
const char* MQTT_BROKER = "192.168.0.109";
const int   MQTT_PORT   = 1883;
const char* CLIENT_ID   = "MASTER_TTGO_GATEWAY";
const char* TOPIC_PUB   = "datos/sensores";

const unsigned long T_MESH_LISTEN  = 5000;   // ms
const unsigned long T_HTTP_POLL    = 3000;   // ms entre polls HTTP
const unsigned long T_HEARTBEAT    = 8000;
const unsigned long T_PANTALLA     = 600000; // 10 min
const int           BROADCAST_N    = 3;
const int           TZ_OFFSET_HRS  = -6;

uint8_t BROADCAST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ───────────────────────────────────────────────
//  HARDWARE
// ───────────────────────────────────────────────
#define PIN_BACKLIGHT 4
#define PIN_BTN_LEFT  0
#define PIN_BTN_RIGHT 35

TFT_eSPI tft = TFT_eSPI();
WiFiClient   wifiClient;
PubSubClient mqttClient(wifiClient);

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
  Serial.printf("[WIFI] Conectando a %s...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

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

    wifiOk = true;
    canalActual = WiFi.channel();
    // [FIX 2] forzar canal de ESP-NOW al del AP
    esp_wifi_set_channel(canalActual, WIFI_SECOND_CHAN_NONE);

    Serial.printf("[WIFI] OK ip:%s ch:%d (modem-sleep OFF)\n",
                  WiFi.localIP().toString().c_str(), canalActual);
    return true;
  }
  wifiOk = false;
  Serial.println("[WIFI] Sin conexion");
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
    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
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
  tft.println("v18.2 / C++");
  digitalWrite(PIN_BACKLIGHT, HIGH);
  delay(2000);
}

void uiStatus(const char* l1, const char* l2, uint16_t c1, uint16_t c2) {
  tft.fillScreen(COLOR_NEGRO);
  tft.setTextSize(2);
  tft.setTextColor(COLOR_VERDE, COLOR_NEGRO);
  tft.setCursor(4, 4);
  tft.print("PIF MASTER v18.2");
  tft.setTextColor(c1, COLOR_NEGRO);
  tft.setCursor(4, 30);
  tft.print(l1);
  if (l2) {
    tft.setTextColor(c2, COLOR_NEGRO);
    tft.setCursor(4, 60);
    tft.print(l2);
  }
  digitalWrite(PIN_BACKLIGHT, HIGH);
}

void uiHeartbeat() {
  tft.fillScreen(COLOR_NEGRO);
  tft.setTextSize(2);
  tft.setTextColor(COLOR_VERDE, COLOR_NEGRO);
  tft.setCursor(4, 4);
  tft.print("Master v18.2");

  tft.setTextSize(1);
  tft.setTextColor(COLOR_GRIS, COLOR_NEGRO);
  tft.setCursor(180, 8);
  tft.print(horaLocal(false));

  tft.setTextSize(2);
  tft.setTextColor(COLOR_AMARILLO, COLOR_NEGRO);
  tft.setCursor(4, 30);
  tft.printf("Nodos: %d", nodosCount);

  tft.setTextColor(COLOR_CYAN, COLOR_NEGRO);
  tft.setCursor(4, 55);
  tft.printf("ch:%d wifi:%s", canalActual, wifiOk ? "OK" : "--");

  tft.setTextColor(COLOR_GRIS, COLOR_NEGRO);
  tft.setCursor(4, 85);
  tft.printf("cola_sub:%d mqtt:%s", colaSubidaCount, mqttOk ? "OK" : "--");

  digitalWrite(PIN_BACKLIGHT, HIGH);
  delay(800);
  digitalWrite(PIN_BACKLIGHT, LOW);
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

  tft.setTextSize(2);
  tft.setTextColor(COLOR_AMARILLO, COLOR_NEGRO);
  tft.setCursor(4, 35);
  tft.printf("Nodos: %d", nodosCount);

  tft.setTextSize(1);
  tft.setTextColor(COLOR_GRIS, COLOR_NEGRO);
  for (int i = 0; i < nodosCount && i < 4; i++) {
    tft.setCursor(4, 65 + i * 15);
    tft.printf("%s %s", nodosVistos[i].id.c_str(), nodosVistos[i].via.c_str());
  }

  digitalWrite(PIN_BACKLIGHT, HIGH);
}

// ───────────────────────────────────────────────
//  BOTONES
// ───────────────────────────────────────────────
void pollBotones() {
  static unsigned long ultimoLeft  = 0;
  static unsigned long ultimoRight = 0;
  unsigned long ahora = millis();
  if (digitalRead(PIN_BTN_LEFT) == LOW && ahora - ultimoLeft > 500) {
    ultimoLeft = ahora;
    flagMesh = true;
  }
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
  Serial.println("\n=== PIF MASTER v18.2 / Arduino C++ ===");

  pinMode(PIN_BACKLIGHT, OUTPUT);
  pinMode(PIN_BTN_LEFT,  INPUT_PULLUP);
  pinMode(PIN_BTN_RIGHT, INPUT_PULLUP);

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
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setKeepAlive(45);
  mqttClient.setSocketTimeout(5);

  // 4) NTP
  configTime(0, 0, "pool.ntp.org");
  Serial.println("[NTP] solicitado");

  uiStatus("Listo", "PIF Mesh activo", COLOR_VERDE, COLOR_GRIS);
  delay(1500);
  digitalWrite(PIN_BACKLIGHT, LOW);
  Serial.println("[OK] Master listo, entrando al loop\n");
}

// ───────────────────────────────────────────────
//  LOOP
// ───────────────────────────────────────────────
void loop() {
  pollBotones();

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
    digitalWrite(PIN_BACKLIGHT, HIGH);
    uiStatus("Servidor", "publicando...", COLOR_AMARILLO, COLOR_GRIS);
    String ts = horaLocal(true);
    encolarSubida(ts + "," + CLIENT_ID + ",T:-- H:--,sensor");
    encolarStatus(ts);
    if (WiFi.status() != WL_CONNECTED) conectarWifi();
    publicarMqtt();
    uiPantallaCompleta();
    delay(3000);
    digitalWrite(PIN_BACKLIGHT, LOW);
  }

  // ── Botón izquierdo: WAVE manual ──
  if (flagMesh) {
    flagMesh = false;
    digitalWrite(PIN_BACKLIGHT, HIGH);
    uiStatus("Malla", "WAVE manual...", COLOR_AMARILLO, COLOR_GRIS);
    ventanaMesh("REQ:ALL", "ALL");
    if (colaSubidaCount > 0) publicarMqtt();
    digitalWrite(PIN_BACKLIGHT, LOW);
  }

  // ── HTTP polling cada T_HTTP_POLL ──
  if (millis() - ultimoHttpPoll > T_HTTP_POLL) {
    ultimoHttpPoll = millis();
    if (WiFi.status() != WL_CONNECTED) conectarWifi();
    bool huboCmd = consultarHttp();
    if (huboCmd && colaBajadaCount > 0) {
      digitalWrite(PIN_BACKLIGHT, HIGH);
      uiStatus("Comando RX", "ejecutando...", COLOR_CYAN, COLOR_AMARILLO);
      ventanaMesh("REQ:ALL", "ALL");
      if (colaSubidaCount > 0) publicarMqtt();
      digitalWrite(PIN_BACKLIGHT, LOW);
    }
    if (colaSubidaCount > 0) publicarMqtt();
  }

  // ── Heartbeat ──
  if (millis() - ultimoHeartbeat > T_HEARTBEAT) {
    ultimoHeartbeat = millis();
    uiHeartbeat();
    Serial.printf("[HEARTBEAT] nodos:%d cola_sub:%d cola_baj:%d wifi:%s mqtt:%s\n",
                  nodosCount, colaSubidaCount, colaBajadaCount,
                  WiFi.status() == WL_CONNECTED ? "OK" : "--",
                  mqttClient.connected() ? "OK" : "--");
  }

  // ── Pantalla cada 10 min ──
  if (millis() - ultimaPantalla > T_PANTALLA) {
    ultimaPantalla = millis();
    String ts = horaLocal(true);
    encolarStatus(ts);
    uiPantallaCompleta();
    delay(3000);
    digitalWrite(PIN_BACKLIGHT, LOW);
  }

  delay(10);
}
