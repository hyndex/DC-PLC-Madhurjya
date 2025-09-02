// ESP32-S3 Control Pilot helper: PWM generation, CP ADC read, UART JSON protocol
// Board: ESP32-S3-DevKitC-1

#include <Arduino.h>
#include <ArduinoJson.h>

// ----- PWM Configuration for the Control Pilot -----
#define CP_1_PWM_PIN 38
#define CP_1_PWM_CHANNEL 0
#define CP_1_PWM_FREQUENCY 1000
#define CP_1_PWM_RESOLUTION 12
#define CP_1_MAX_DUTY_CYCLE 4095

// ----- CP ADC Read Pin -----
#define CP_1_READ_PIN 1
#define CP_1_ADC_CHANNEL 0
// Thresholds in mV for states A..F (A=highest voltage)
#define CP_1_ADC_THRESHOLD_12 2300
#define CP_1_ADC_THRESHOLD_9  2000
#define CP_1_ADC_THRESHOLD_6  1700
#define CP_1_ADC_THRESHOLD_3  1450
#define CP_1_ADC_THRESHOLD_0  1250
// Hysteresis in mV to avoid rapid state flapping near thresholds
#define CP_1_ADC_HYSTERESIS   100

// ----- UART Pins (to Raspberry Pi) -----
#define ESP_UART_RX 44
#define ESP_UART_TX 43

// Use UART1 for the Pi link to keep USB-CDC (Serial) for logs
HardwareSerial SerialPi(1);

// ---- Peripheral JSON-RPC state ----
struct Meter { float v; float i; float p; float e; };
enum ModePeriph { MODE_SIM = 0, MODE_HW = 1 };
static ModePeriph g_periph_mode = MODE_SIM;
static bool g_contactor_cmd = false;
static bool g_contactor_aux = false;
static uint32_t g_armed_until_ms = 0;
static bool g_meter_stream = false;
static bool g_temps_stream = false;
static uint32_t g_last_ping_ms = 0;
static uint32_t g_up0_ms = 0;

// State
enum class OpMode : uint8_t { MANUAL = 0, DC_AUTO = 1 };
static volatile OpMode g_mode = OpMode::DC_AUTO;  // default: DC fast charging helper
static volatile bool g_pwm_enabled = false;       // used in MANUAL only
static volatile uint16_t g_pwm_duty_pct = 0;      // used in MANUAL only, 0..100
static volatile uint32_t g_pwm_freq_hz = CP_1_PWM_FREQUENCY;

static uint32_t g_last_status_ms = 0;
static char g_last_cp_state = 'A';
static int g_last_cp_mv = 0;
static uint16_t g_last_output_duty_pct = 100; // effective output duty applied on CP line
static uint32_t g_last_usb_log_ms = 0;
static int g_last_cp_mv_min = 0;
static int g_last_cp_mv_avg = 0;
// Robust filtering across loops
static int g_mv_max_hist[6] = {0};
static uint8_t g_mv_max_hist_count = 0;
static uint8_t g_mv_max_hist_idx = 0;
static char g_pending_state = 'A';
static uint8_t g_pending_count = 0;
static uint32_t g_sample_phase_us = 0; // desynchronize burst sampling vs PWM

// USB log cadence (ms)
#ifndef USB_LOG_PERIOD_MS
#define USB_LOG_PERIOD_MS 1000
#endif

// ADC sampling parameters for plateau capture
#ifndef CP_SAMPLE_COUNT
#define CP_SAMPLE_COUNT 256
#endif
#ifndef CP_SAMPLE_DELAY_US
#define CP_SAMPLE_DELAY_US 10
#endif

static inline uint32_t pct_to_duty(uint16_t pct) {
  if (pct == 0) return 0;
  if (pct >= 100) return CP_1_MAX_DUTY_CYCLE;
  return (uint32_t)((CP_1_MAX_DUTY_CYCLE * (uint32_t)pct) / 100U);
}

static void apply_pwm_manual() {
  // In MANUAL mode, when disabled we hold the line high (+12V) via 100% duty
  // When enabled, we use the requested duty percentage
  const uint32_t duty = g_pwm_enabled ? pct_to_duty(g_pwm_duty_pct)
                                      : CP_1_MAX_DUTY_CYCLE;  // idle = high
  ledcWrite(CP_1_PWM_CHANNEL, duty);
}

static void configure_pwm() {
  ledcSetup(CP_1_PWM_CHANNEL, g_pwm_freq_hz, CP_1_PWM_RESOLUTION);
  ledcAttachPin(CP_1_PWM_PIN, CP_1_PWM_CHANNEL);
  // Keep configured mode's output policy
  if (g_mode == OpMode::MANUAL) {
    apply_pwm_manual();
  }
}

static void read_cp_mv_stats(int &min_mv, int &max_mv, int &avg_mv, size_t samples = CP_SAMPLE_COUNT) {
  if (samples == 0) samples = 1;
  int64_t acc = 0;
  int minv = INT32_MAX;
  int maxv = INT32_MIN;
  // Small phase offset to avoid aliasing with PWM period
  if (g_sample_phase_us) delayMicroseconds(g_sample_phase_us);
  for (size_t i = 0; i < samples; ++i) {
    // Warm-up read improves stability on ESP32 ADC
    (void)analogRead(CP_1_READ_PIN);
    delayMicroseconds(CP_SAMPLE_DELAY_US);
    int v = analogReadMilliVolts(CP_1_READ_PIN);
    acc += v;
    if (v < minv) minv = v;
    if (v > maxv) maxv = v;
  }
  min_mv = (minv == INT32_MAX) ? 0 : minv;
  max_mv = (maxv == INT32_MIN) ? 0 : maxv;
  avg_mv = (int)(acc / (int64_t)samples);
  // Advance phase (prime to 1000us for 1kHz PWM); keep small
  g_sample_phase_us = (g_sample_phase_us + 17) % 1000;
}

static char cp_state_from_mv(int mv) {
  if (mv >= CP_1_ADC_THRESHOLD_12) return 'A';
  if (mv >= CP_1_ADC_THRESHOLD_9)  return 'B';
  if (mv >= CP_1_ADC_THRESHOLD_6)  return 'C';
  if (mv >= CP_1_ADC_THRESHOLD_3)  return 'D';
  if (mv >= CP_1_ADC_THRESHOLD_0)  return 'E';
  return 'F';
}

static char cp_state_with_hysteresis(int mv, char last) {
  // If current mv is clearly within a target band beyond hysteresis, switch; otherwise hold last
  switch (last) {
    case 'A':
      if (mv < CP_1_ADC_THRESHOLD_12 - CP_1_ADC_HYSTERESIS) return cp_state_from_mv(mv);
      return 'A';
    case 'B':
      if (mv >= CP_1_ADC_THRESHOLD_12 + CP_1_ADC_HYSTERESIS) return 'A';
      if (mv < CP_1_ADC_THRESHOLD_9 - CP_1_ADC_HYSTERESIS) return cp_state_from_mv(mv);
      return 'B';
    case 'C':
      if (mv >= CP_1_ADC_THRESHOLD_9 + CP_1_ADC_HYSTERESIS) return 'B';
      if (mv < CP_1_ADC_THRESHOLD_6 - CP_1_ADC_HYSTERESIS) return cp_state_from_mv(mv);
      return 'C';
    case 'D':
      if (mv >= CP_1_ADC_THRESHOLD_6 + CP_1_ADC_HYSTERESIS) return 'C';
      if (mv < CP_1_ADC_THRESHOLD_3 - CP_1_ADC_HYSTERESIS) return cp_state_from_mv(mv);
      return 'D';
    case 'E':
      if (mv >= CP_1_ADC_THRESHOLD_3 + CP_1_ADC_HYSTERESIS) return 'D';
      if (mv < CP_1_ADC_THRESHOLD_0 - CP_1_ADC_HYSTERESIS) return 'F';
      return 'E';
    case 'F':
    default:
      if (mv >= CP_1_ADC_THRESHOLD_0 + CP_1_ADC_HYSTERESIS) return 'E';
      return 'F';
  }
}

// Return whether mv is comfortably inside the voltage band for 'st'
static bool mv_strong_in_state(int mv, char st) {
  switch (st) {
    case 'A': return mv >= (CP_1_ADC_THRESHOLD_12 + CP_1_ADC_HYSTERESIS);
    case 'B': return mv >= (CP_1_ADC_THRESHOLD_9 + CP_1_ADC_HYSTERESIS) && mv < (CP_1_ADC_THRESHOLD_12 - CP_1_ADC_HYSTERESIS);
    case 'C': return mv >= (CP_1_ADC_THRESHOLD_6 + CP_1_ADC_HYSTERESIS) && mv < (CP_1_ADC_THRESHOLD_9 - CP_1_ADC_HYSTERESIS);
    case 'D': return mv >= (CP_1_ADC_THRESHOLD_3 + CP_1_ADC_HYSTERESIS) && mv < (CP_1_ADC_THRESHOLD_6 - CP_1_ADC_HYSTERESIS);
    case 'E': return mv >= (CP_1_ADC_THRESHOLD_0 + CP_1_ADC_HYSTERESIS) && mv < (CP_1_ADC_THRESHOLD_3 - CP_1_ADC_HYSTERESIS);
    case 'F': default: return mv < (CP_1_ADC_THRESHOLD_0 - CP_1_ADC_HYSTERESIS);
  }
}

static inline bool is_connected_state(char st) { return (st == 'B' || st == 'C' || st == 'D'); }

// Compute robust max over recent bursts (average of top-2 values)
static int robust_max_mv() {
  if (g_mv_max_hist_count == 0) return g_last_cp_mv; // fallback
  int top1 = 0, top2 = 0;
  for (uint8_t i = 0; i < g_mv_max_hist_count; ++i) {
    int v = g_mv_max_hist[i];
    if (v >= top1) { top2 = top1; top1 = v; }
    else if (v > top2) { top2 = v; }
  }
  if (g_mv_max_hist_count == 1) return top1;
  return (top1 + top2) / 2;
}

static void send_status_json() {
  int smin = 0, smax = 0, savg = 0;
  read_cp_mv_stats(smin, smax, savg);
  // Update history for robust calculation
  g_mv_max_hist[g_mv_max_hist_idx] = smax;
  if (g_mv_max_hist_count < (uint8_t)(sizeof(g_mv_max_hist)/sizeof(g_mv_max_hist[0]))) {
    g_mv_max_hist_count++;
  }
  g_mv_max_hist_idx = (g_mv_max_hist_idx + 1) % (uint8_t)(sizeof(g_mv_max_hist)/sizeof(g_mv_max_hist[0]));
  const int mv_robust = robust_max_mv();
  const int mv = smax; // cp_mv = immediate peak
  const char st = cp_state_from_mv(mv_robust);
  StaticJsonDocument<256> doc;
  doc["type"] = "status";
  doc["cp_mv"] = mv;
  doc["cp_mv_robust"] = mv_robust;
  doc["state"] = String(st);
  doc["mode"] = (g_mode == OpMode::DC_AUTO) ? "dc" : "manual";
  JsonObject pwm = doc.createNestedObject("pwm");
  pwm["enabled"] = g_pwm_enabled;
  pwm["duty"] = g_pwm_duty_pct;
  pwm["hz"] = g_pwm_freq_hz;

  serializeJson(doc, SerialPi);
  SerialPi.print('\n');
}

static void handle_cmd_set_pwm(JsonObject obj) {
  if (g_mode != OpMode::MANUAL) {
    StaticJsonDocument<128> resp;
    resp["type"] = "error";
    resp["msg"] = "mode_dc_auto";
    serializeJson(resp, SerialPi);
    SerialPi.print('\n');
    Serial.print("["); Serial.print(millis()); Serial.print("] [W] set_pwm rejected in dc mode\n");
    return;
  }
  if (obj.containsKey("duty")) {
    int d = obj["duty"].as<int>();
    if (d < 0) d = 0; if (d > 100) d = 100;
    g_pwm_duty_pct = (uint16_t)d;
  }
  if (obj.containsKey("enable")) {
    g_pwm_enabled = obj["enable"].as<bool>();
  }
  apply_pwm_manual();
  Serial.print("["); Serial.print(millis()); Serial.print("] [I] PWM manual updated: enable=");
  Serial.print(g_pwm_enabled);
  Serial.print(" duty%="); Serial.print(g_pwm_duty_pct);
  Serial.print(" hz="); Serial.println(g_pwm_freq_hz);
  send_status_json();
}

static void handle_cmd_enable_pwm(JsonObject obj) {
  if (g_mode != OpMode::MANUAL) {
    StaticJsonDocument<128> resp;
    resp["type"] = "error";
    resp["msg"] = "mode_dc_auto";
    serializeJson(resp, SerialPi);
    SerialPi.print('\n');
    Serial.print("["); Serial.print(millis()); Serial.print("] [W] enable_pwm rejected in dc mode\n");
    return;
  }
  g_pwm_enabled = obj["enable"].as<bool>();
  apply_pwm_manual();
  Serial.print("["); Serial.print(millis()); Serial.print("] [I] PWM enable set to ");
  Serial.println(g_pwm_enabled ? "true" : "false");
  send_status_json();
}

static void handle_cmd_set_freq(JsonObject obj) {
  uint32_t hz = obj["hz"].as<uint32_t>();
  if (hz < 500) hz = 500; // conservative limits
  if (hz > 5000) hz = 5000;
  g_pwm_freq_hz = hz;
  configure_pwm();
  Serial.print("["); Serial.print(millis()); Serial.print("] [I] PWM freq set to ");
  Serial.print(g_pwm_freq_hz); Serial.println(" Hz");
  send_status_json();
}

static void handle_cmd_set_mode(JsonObject obj) {
  const char* m = obj["mode"] | "";
  if (!strcmp(m, "dc")) {
    g_mode = OpMode::DC_AUTO;
  } else if (!strcmp(m, "manual")) {
    g_mode = OpMode::MANUAL;
  } else {
    StaticJsonDocument<96> resp;
    resp["type"] = "error";
    resp["msg"] = "bad_mode";
    serializeJson(resp, SerialPi);
    SerialPi.print('\n');
    Serial.print("["); Serial.print(millis()); Serial.print("] [E] set_mode invalid value: ");
    Serial.println(m);
    return;
  }
  Serial.print("["); Serial.print(millis()); Serial.print("] [I] Mode set to ");
  Serial.println((g_mode == OpMode::DC_AUTO) ? "dc" : "manual");
  send_status_json();
}

static void apply_dc_auto_output(char st) {
  // Idle (A) and fault (E/F) -> keep line high (+12V)
  // Connected B/C/D -> fixed 5% PWM per CCS DC guidance
  uint32_t duty = CP_1_MAX_DUTY_CYCLE; // default high
  switch (st) {
    case 'B':
    case 'C':
    case 'D':
      duty = pct_to_duty(5);
      break;
    case 'A':
    case 'E':
    case 'F':
    default:
      duty = CP_1_MAX_DUTY_CYCLE;
      break;
  }
  ledcWrite(CP_1_PWM_CHANNEL, duty);
}

static void process_line(String &line) {
  StaticJsonDocument<768> doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) {
    StaticJsonDocument<128> resp;
    resp["type"] = "error";
    resp["msg"] = String("bad_json:") + err.c_str();
    serializeJson(resp, SerialPi);
    SerialPi.print('\n');
    Serial.print("["); Serial.print(millis()); Serial.print("] [E] Bad JSON: "); Serial.println(err.c_str());
    return;
  }

  // JSON-RPC path (peripheral)
  const char* mtype = doc["type"] | "";
  if (strcmp(mtype, "req") == 0) {
    uint32_t id = doc["id"] | 0;
    const char* method = doc["method"] | "";
    if (!method[0]) {
      StaticJsonDocument<128> errj; errj["code"] = -32600; errj["message"] = "invalid_request";
      StaticJsonDocument<192> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["error"] = errj;
      serializeJson(out, SerialPi); SerialPi.print('\n'); return;
    }
    // sys.ping
    if (!strcmp(method, "sys.ping")) {
      g_last_ping_ms = millis();
      StaticJsonDocument<256> res; res["up_ms"] = millis()-g_up0_ms; res["mode"]=(g_periph_mode==MODE_SIM)?"sim":"hw"; res.createNestedObject("temps")["mcu"] = temperatureRead();
      StaticJsonDocument<256> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=res;
      serializeJson(out, SerialPi); SerialPi.print('\n'); return;
    }
    if (!strcmp(method, "sys.info")) {
      StaticJsonDocument<384> res; res["fw"]="esp-cp-periph/0.2.0"; res["proto"]=1; res["mode"]=(g_periph_mode==MODE_SIM)?"sim":"hw";
      JsonArray caps = res.createNestedArray("capabilities"); caps.add("cp"); caps.add("contactor"); caps.add("temps.gun_a"); caps.add("temps.gun_b"); caps.add("meter");
      StaticJsonDocument<512> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=res;
      serializeJson(out, SerialPi); SerialPi.print('\n'); return;
    }
    if (!strcmp(method, "sys.arm")) {
      g_armed_until_ms = millis() + 1500;
      StaticJsonDocument<96> res; res["armed_until_ms"]=g_armed_until_ms; StaticJsonDocument<192> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=res; serializeJson(out, SerialPi); SerialPi.print('\n'); return;
    }
    if (!strcmp(method, "sys.set_mode")) {
      const char* m = doc["params"]["mode"] | "sim"; g_periph_mode = (!strcmp(m,"hw"))? MODE_HW : MODE_SIM; StaticJsonDocument<96> res; res["mode"]= (g_periph_mode==MODE_SIM)?"sim":"hw"; StaticJsonDocument<192> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=res; serializeJson(out, SerialPi); SerialPi.print('\n'); return;
    }
    if (!strcmp(method, "contactor.check")) {
      StaticJsonDocument<256> res; res["commanded"]=g_contactor_cmd; bool aux=(g_contactor_aux==g_contactor_cmd); res["aux_ok"]=aux; res["coil_ma"]= g_contactor_cmd ? 120.0 : 0.0; res["reason"]= aux?"ok":"mismatch"; StaticJsonDocument<256> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=res; serializeJson(out, SerialPi); SerialPi.print('\n'); return;
    }
    if (!strcmp(method, "contactor.set")) {
      if ((int32_t)(millis() - g_armed_until_ms) > 0) { StaticJsonDocument<128> errj; errj["code"]=1001; errj["message"]="not_armed"; StaticJsonDocument<192> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["error"]=errj; serializeJson(out, SerialPi); SerialPi.print('\n'); return; }
      bool on = doc["params"]["on"] | false; g_contactor_cmd = on; delay(40); g_contactor_aux = on; delay(60); bool aux_ok=(g_contactor_aux==g_contactor_cmd);
      if (!aux_ok && on) { g_contactor_cmd=false; g_contactor_aux=false; StaticJsonDocument<128> errj; errj["code"]=1002; errj["message"]="aux_mismatch"; StaticJsonDocument<192> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["error"]=errj; serializeJson(out, SerialPi); SerialPi.print('\n'); return; }
      StaticJsonDocument<128> res; res["ok"]=true; res["aux_ok"]=aux_ok; res["took_ms"]=60; StaticJsonDocument<192> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=res; serializeJson(out, SerialPi); SerialPi.print('\n'); return;
    }
    if (!strcmp(method, "temps.read")) {
      StaticJsonDocument<256> res; JsonObject t = res.createNestedObject("temps"); t.createNestedObject("gun_a")["c"] = 32.0 + (g_contactor_aux? 12.0:0.5); t.createNestedObject("gun_b")["c"] = 31.5 + (g_contactor_aux? 11.0:0.3);
      StaticJsonDocument<256> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=res; serializeJson(out, SerialPi); SerialPi.print('\n'); return;
    }
    if (!strcmp(method, "meter.read")) {
      static float e=0.0f; float on=g_contactor_aux?1.0f:0.0f; float v=415.0f; float i= on*50.0f; float p=v*i/1000.0f; e += p*0.001f; StaticJsonDocument<256> res; res["v"]=v; res["i"]=i; res["p"]=p; res["e"]=e; StaticJsonDocument<256> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=res; serializeJson(out, SerialPi); SerialPi.print('\n'); return;
    }
    if (!strcmp(method, "meter.stream_start")) { g_meter_stream = true; StaticJsonDocument<64> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=JsonObject(); serializeJson(out, SerialPi); SerialPi.print('\n'); return; }
    if (!strcmp(method, "meter.stream_stop"))  { g_meter_stream = false; StaticJsonDocument<64> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=JsonObject(); serializeJson(out, SerialPi); SerialPi.print('\n'); return; }
    if (!strcmp(method, "temps.stream_start")) { g_temps_stream = true; StaticJsonDocument<64> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=JsonObject(); serializeJson(out, SerialPi); SerialPi.print('\n'); return; }
    if (!strcmp(method, "temps.stream_stop"))  { g_temps_stream = false; StaticJsonDocument<64> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["result"]=JsonObject(); serializeJson(out, SerialPi); SerialPi.print('\n'); return; }
    // unknown JSON-RPC
    StaticJsonDocument<128> errj; errj["code"]= -32601; errj["message"]= "unknown_method"; StaticJsonDocument<192> out; out["type"]="res"; out["id"]=id; out["ts"]=millis(); out["error"]=errj; serializeJson(out, SerialPi); SerialPi.print('\n'); return;
  }

  // Legacy CP command path
  const char* cmd = doc["cmd"] | "";
  if (!cmd[0]) {
    StaticJsonDocument<96> resp; resp["type"] = "error"; resp["msg"] = "missing_cmd"; serializeJson(resp, SerialPi); SerialPi.print('\n'); Serial.print("["); Serial.print(millis()); Serial.println("] [E] Missing cmd field"); return;
  }
  String scmd(cmd);
  Serial.print("["); Serial.print(millis()); Serial.print("] [D] RX cmd: "); Serial.println(scmd);
  if (scmd == "set_pwm")      { handle_cmd_set_pwm(doc.as<JsonObject>()); }
  else if (scmd == "enable_pwm") { handle_cmd_enable_pwm(doc.as<JsonObject>()); }
  else if (scmd == "set_freq")   { handle_cmd_set_freq(doc.as<JsonObject>()); }
  else if (scmd == "set_mode")   { handle_cmd_set_mode(doc.as<JsonObject>()); }
  else if (scmd == "get_status") { send_status_json(); }
  else if (scmd == "ping")      { StaticJsonDocument<64> resp; resp["type"]="pong"; serializeJson(resp, SerialPi); SerialPi.print('\n'); }
  else if (scmd == "restart_slac_hint") {
    uint32_t ms = doc["ms"] | 400; if (ms < 50) ms = 50; if (ms > 2000) ms = 2000; OpMode prev = g_mode; g_mode = OpMode::MANUAL; g_pwm_enabled = true; g_pwm_duty_pct = 100; apply_pwm_manual(); delay(ms); g_mode = OpMode::DC_AUTO; apply_dc_auto_output(g_last_cp_state); StaticJsonDocument<96> resp; resp["type"]="ok"; resp["cmd"]="restart_slac_hint"; serializeJson(resp, SerialPi); SerialPi.print('\n'); send_status_json(); (void)prev; }
  else if (scmd == "reset")     { StaticJsonDocument<64> resp; resp["type"]="ok"; resp["cmd"]="reset"; serializeJson(resp, SerialPi); SerialPi.print('\n'); delay(50); ESP.restart(); }
  else { StaticJsonDocument<96> resp; resp["type"]="error"; resp["msg"]="unknown_cmd"; serializeJson(resp, SerialPi); SerialPi.print('\n'); Serial.print("["); Serial.print(millis()); Serial.print("] [E] Unknown cmd: "); Serial.println(scmd); }
}

void setup() {
  // USB-CDC for debug
  Serial.begin(115200);
  while (!Serial && millis() < 1500) { /* wait for USB */ }
  Serial.println("ESP32-S3 CP Helper booting...");

  // UART to Raspberry Pi
  SerialPi.begin(115200, SERIAL_8N1, ESP_UART_RX, ESP_UART_TX);
  g_up0_ms = millis();

  // Configure ADC and PWM
  pinMode(CP_1_READ_PIN, INPUT);
  analogReadResolution(12); // ensure 12-bit resolution baseline
  analogSetPinAttenuation(CP_1_READ_PIN, ADC_11db);

  configure_pwm();
  // Ensure idle=high at boot regardless of first measurement timing
  ledcWrite(CP_1_PWM_CHANNEL, CP_1_MAX_DUTY_CYCLE);
  Serial.println("Init done.");
}

void loop() {
  // Periodic status
  const uint32_t now = millis();
  if (now - g_last_status_ms >= 200) {
    g_last_status_ms = now;
    // Update outputs based on mode and latest measured state
    int smin = 0, smax = 0, savg = 0;
    read_cp_mv_stats(smin, smax, savg);
    // push into history for robust filtering
    g_mv_max_hist[g_mv_max_hist_idx] = smax;
    if (g_mv_max_hist_count < (uint8_t)(sizeof(g_mv_max_hist)/sizeof(g_mv_max_hist[0]))) {
      g_mv_max_hist_count++;
    }
    g_mv_max_hist_idx = (g_mv_max_hist_idx + 1) % (uint8_t)(sizeof(g_mv_max_hist)/sizeof(g_mv_max_hist[0]));
    const int mv_robust = robust_max_mv();
    const int mv = smax; // report immediate peak; use robust for state
    const char prev = g_last_cp_state;
    const char cand = cp_state_from_mv(mv_robust);
    // Treat sudden very-low max (missed plateau) as transient if previously connected
    bool transient_low = is_connected_state(prev) && (smax < (CP_1_ADC_THRESHOLD_0 - 150));
    // Debounce: require multiple confirmations unless strongly in new band
    const uint8_t confirm_needed = mv_strong_in_state(mv_robust, cand) ? 1 : 3;
    if (!transient_low) {
      if (cand != prev) {
        if (g_pending_state == cand) {
          if (g_pending_count + 1 >= confirm_needed) {
            g_last_cp_state = cand;
            g_pending_count = 0;
          } else {
            g_pending_count++;
          }
        } else {
          g_pending_state = cand;
          g_pending_count = 1;
        }
      } else {
        g_pending_count = 0;
        g_pending_state = cand;
        g_last_cp_state = cand;
      }
    } else {
      // keep previous state; slowly decay pending
      if (g_pending_count > 0) g_pending_count--;
    }
    const char st = g_last_cp_state;
    if (g_mode == OpMode::DC_AUTO) {
      apply_dc_auto_output(st);
    }
    // Track effective output duty
    if (g_mode == OpMode::DC_AUTO) {
      g_last_output_duty_pct = (st == 'B' || st == 'C' || st == 'D') ? 5 : 100;
    } else {
      g_last_output_duty_pct = g_pwm_enabled ? g_pwm_duty_pct : 100;
    }
    g_last_cp_mv = mv;
    g_last_cp_mv_min = smin;
    g_last_cp_mv_avg = savg;
    // Event: CP state transition
    if (st != prev) {
      Serial.print("["); Serial.print(now); Serial.print("] [I] CP state ");
      Serial.print(prev); Serial.print(" -> "); Serial.print(st);
      Serial.print(" at "); Serial.print(mv); Serial.print(" mV (robust="); Serial.print(mv_robust); Serial.println(" mV)");
    }
    // Report after applying
    StaticJsonDocument<256> doc;
    doc["type"] = "status";
    doc["cp_mv"] = mv;
    doc["cp_mv_robust"] = mv_robust;
    doc["state"] = String(st);
    doc["mode"] = (g_mode == OpMode::DC_AUTO) ? "dc" : "manual";
    JsonObject pwm = doc.createNestedObject("pwm");
    pwm["enabled"] = g_pwm_enabled;
    pwm["duty"] = g_pwm_duty_pct;
    pwm["hz"] = g_pwm_freq_hz;
    serializeJson(doc, SerialPi);
    SerialPi.print('\n');
  }

  // Periodic USB human-readable log (throttled)
  if (now - g_last_usb_log_ms >= USB_LOG_PERIOD_MS) {
    g_last_usb_log_ms = now;
    Serial.print("["); Serial.print(now); Serial.print("] [S] ");
    Serial.print("mv_max="); Serial.print(g_last_cp_mv);
    Serial.print(" mv_min="); Serial.print(g_last_cp_mv_min);
    Serial.print(" mv_avg="); Serial.print(g_last_cp_mv_avg);
    Serial.print(" state="); Serial.print(g_last_cp_state);
    Serial.print(" mode="); Serial.print((g_mode == OpMode::DC_AUTO) ? "dc" : "manual");
    Serial.print(" pwm: en="); Serial.print(g_pwm_enabled);
    Serial.print(" duty%="); Serial.print(g_pwm_duty_pct);
    Serial.print(" hz="); Serial.print(g_pwm_freq_hz);
    Serial.print(" outDuty%="); Serial.println(g_last_output_duty_pct);
  }

  // Read commands (newline-delimited JSON)
  static String line;
  while (SerialPi.available() > 0) {
    char c = (char)SerialPi.read();
    if (c == '\n') {
      if (line.length() > 0) {
        process_line(line);
        line = "";
      }
    } else if (c != '\r') {
      // prevent runaway lines
      if (line.length() < 240) line += c; else line = "";
    }
  }

  // Peripheral streams
  static uint32_t last_periph_tick = 0;
  if (now - last_periph_tick >= 1000) {
    last_periph_tick = now;
    if (g_meter_stream) {
      static float e=0.0f; float on=g_contactor_aux?1.0f:0.0f; float v=415.0f; float i=on*50.0f; float p=v*i/1000.0f; e += p*0.001f; StaticJsonDocument<192> pld; pld["v"]=v; pld["i"]=i; pld["p"]=p; pld["e"]=e; StaticJsonDocument<256> evt; evt["type"]="evt"; evt["ts"]=now; evt["id"]=0; evt["method"]="evt:meter.tick"; evt["result"]=pld; serializeJson(evt, SerialPi); SerialPi.print('\n');
    }
    if (g_temps_stream) {
      StaticJsonDocument<192> pld; pld.createNestedObject("gun_a")["c"] = 32.0 + (g_contactor_aux?12.0:0.5); pld.createNestedObject("gun_b")["c"] = 31.5 + (g_contactor_aux?11.0:0.3); StaticJsonDocument<256> evt; evt["type"]="evt"; evt["ts"]=now; evt["id"]=0; evt["method"]="evt:temps.tick"; evt["result"]=pld; serializeJson(evt, SerialPi); SerialPi.print('\n');
    }
  }

  // Keepalive failsafe for contactor
  if ((now - g_last_ping_ms) > 6000 && g_contactor_cmd) {
    g_contactor_cmd = false; g_contactor_aux = false;
    StaticJsonDocument<96> evt; evt["type"]="evt"; evt["ts"]=now; evt["id"]=0; evt["method"]="evt:failsafe.keepalive"; JsonObject res = evt.createNestedObject("result"); res["forced"] = "contactor_off"; serializeJson(evt, SerialPi); SerialPi.print('\n');
  }
}
