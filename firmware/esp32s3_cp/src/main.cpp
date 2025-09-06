// ESP32-S3 Control Pilot helper: PWM generation, CP ADC read, UART JSON protocol
// Board: ESP32-S3-DevKitC-1

#include <Arduino.h>
#include <ArduinoJson.h>
// Reduce RF/digital noise impact on ADC by disabling radios
#include <WiFi.h>
#include "esp_wifi.h"
#include "esp_bt.h"

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
// Simple thresholds requested for trial (mV)
#define CP_1_ADC_THRESHOLD_12 2400
#define CP_1_ADC_THRESHOLD_9  2000
#define CP_1_ADC_THRESHOLD_6  1700
#define CP_1_ADC_THRESHOLD_3  1450
#define CP_1_ADC_THRESHOLD_0  1250
// Wider hysteresis to suppress flicker with PWM ripple
#define CP_1_ADC_HYSTERESIS   150
// Separate, smaller hysteresis for leaving A into B (more responsive)
#ifndef CP_1_ADC_HYSTERESIS_A2B
#define CP_1_ADC_HYSTERESIS_A2B 100
#endif

// Robust plateau estimator over a burst: keep a larger top-K window,
// then compute a trimmed mean from the high side to avoid edge overshoot.
#ifndef CP_TOPK_IN_BURST
#define CP_TOPK_IN_BURST 24   // larger K improves robustness against edge spikes
#endif
// Optionally widen hysteresis if you still see edges near thresholds
// #undef CP_1_ADC_HYSTERESIS
// #define CP_1_ADC_HYSTERESIS 150

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
static int g_last_cp_mv_robust = 0;
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

// Runtime-adjustable thresholds (initialized from compile-time defaults)
static int g_th_12 = CP_1_ADC_THRESHOLD_12;
static int g_th_9  = CP_1_ADC_THRESHOLD_9;
static int g_th_6  = CP_1_ADC_THRESHOLD_6;
static int g_th_3  = CP_1_ADC_THRESHOLD_3;
static int g_th_0  = CP_1_ADC_THRESHOLD_0;
static int g_hys   = CP_1_ADC_HYSTERESIS;
static int g_hys_ab= CP_1_ADC_HYSTERESIS_A2B;

// Disable Wi-Fi and BLE to reduce ADC jitter on ESP32-S3
static void disable_radios() {
  // Wi-Fi off
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
  esp_wifi_stop();

  // BLE off (ESP32-S3 has BLE)
  if (esp_bt_controller_get_status() == ESP_BT_CONTROLLER_STATUS_ENABLED) {
    esp_bt_controller_disable();
  }
  // Free BLE controller memory so no background tasks remain
  esp_bt_controller_mem_release(ESP_BT_MODE_BLE);
}

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
  int maxtrue = INT32_MIN;
  // Maintain a small ascending array of the top-K values (approximate plateau)
  int topk[CP_TOPK_IN_BURST];
  uint8_t tk = 0;
  auto insert_topk = [&](int v) {
    if (tk < CP_TOPK_IN_BURST) {
      topk[tk++] = v;
      for (int i = tk - 1; i > 0 && topk[i] < topk[i - 1]; --i) {
        int t = topk[i]; topk[i] = topk[i - 1]; topk[i - 1] = t;
      }
    } else if (v > topk[0]) {
      topk[0] = v;
      for (int i = 0; i + 1 < CP_TOPK_IN_BURST && topk[i] > topk[i + 1]; ++i) {
        int t = topk[i]; topk[i] = topk[i + 1]; topk[i + 1] = t;
      }
    }
  };
  // Small phase offset to avoid aliasing with PWM period
  if (g_sample_phase_us) delayMicroseconds(g_sample_phase_us);
  for (size_t i = 0; i < samples; ++i) {
    (void)analogRead(CP_1_READ_PIN);           // warm-up read
    delayMicroseconds(CP_SAMPLE_DELAY_US);
    int v = analogReadMilliVolts(CP_1_READ_PIN);
    acc += v;
    if (v < minv)     minv = v;
    if (v > maxtrue)  maxtrue = v;
    insert_topk(v);
  }
  // Compute robust plateau estimate: trimmed mean of upper half excluding top outliers
  int robust_max = 0;
  if (tk == 0) {
    robust_max = (maxtrue == INT32_MIN) ? 0 : maxtrue;
  } else {
    int start = tk / 2;                 // keep upper half
    int hi_exclude = (tk >= 6) ? 2 : 1; // drop 1–2 highest to avoid overshoot
    int end = tk - hi_exclude;          // [start, end)
    if (end <= start) { start = (tk > 3) ? (tk - 3) : 0; end = tk - 1; if (end <= start) { start = 0; end = tk; } }
    int64_t sum = 0; int n = 0;
    for (int i = start; i < end; ++i) { sum += topk[i]; ++n; }
    robust_max = (n > 0) ? (int)(sum / n) : topk[tk - 1];
  }
  min_mv = (minv == INT32_MAX) ? 0 : minv;
  max_mv = robust_max;                 // return robust plateau as "max"
  avg_mv = (int)(acc / (int64_t)samples);
  // Advance phase (co-prime-ish to 1000us for 1kHz PWM)
  g_sample_phase_us = (g_sample_phase_us + 53) % 1000;
}

static char cp_state_from_mv(int mv) {
  if (mv >= g_th_12) return 'A';
  if (mv >= g_th_9)  return 'B';
  if (mv >= g_th_6)  return 'C';
  if (mv >= g_th_3)  return 'D';
  if (mv >= g_th_0)  return 'E';
  return 'F';
}

static char cp_state_with_hysteresis(int mv, char last) {
  // If current mv is clearly within a target band beyond hysteresis, switch; otherwise hold last
  switch (last) {
    case 'A':
      // Use a smaller hysteresis to enter B from A so we don't lag around the boundary
      if (mv < g_th_12 - g_hys_ab) return cp_state_from_mv(mv);
      return 'A';
    case 'B':
      if (mv >= g_th_12 + g_hys) return 'A';
      if (mv < g_th_9 - g_hys) return cp_state_from_mv(mv);
      return 'B';
    case 'C':
      if (mv >= g_th_9 + g_hys) return 'B';
      if (mv < g_th_6 - g_hys) return cp_state_from_mv(mv);
      return 'C';
    case 'D':
      if (mv >= g_th_6 + g_hys) return 'C';
      if (mv < g_th_3 - g_hys) return cp_state_from_mv(mv);
      return 'D';
    case 'E':
      if (mv >= g_th_3 + g_hys) return 'D';
      if (mv < g_th_0 - g_hys) return 'F';
      return 'E';
    case 'F':
    default:
      if (mv >= g_th_0 + g_hys) return 'E';
      return 'F';
  }
}

// Return whether mv is comfortably inside the voltage band for 'st'
static bool mv_strong_in_state(int mv, char st) {
  switch (st) {
    case 'A': return mv >= (g_th_12 + g_hys);
    case 'B': return mv >= (g_th_9 + g_hys) && mv < (g_th_12 - g_hys);
    case 'C': return mv >= (g_th_6 + g_hys) && mv < (g_th_9 - g_hys);
    case 'D': return mv >= (g_th_3 + g_hys) && mv < (g_th_6 - g_hys);
    case 'E': return mv >= (g_th_0 + g_hys) && mv < (g_th_3 - g_hys);
    case 'F': default: return mv < (g_th_0 - g_hys);
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
  const int mv = g_last_cp_mv;
  const int mv_robust = g_last_cp_mv_robust;
  StaticJsonDocument<256> doc;
  doc["type"] = "status";
  doc["cp_mv"] = mv;
  doc["cp_mv_robust"] = mv_robust;
  doc["state"] = String(g_last_cp_state);
  doc["mode"] = (g_mode == OpMode::DC_AUTO) ? "dc" : "manual";
  JsonObject pwm = doc.createNestedObject("pwm");
  pwm["enabled"] = g_pwm_enabled;
  pwm["duty"] = g_pwm_duty_pct;
  pwm["hz"] = g_pwm_freq_hz;
  pwm["out"] = g_last_output_duty_pct; // effective output duty percentage
  JsonObject thr = doc.createNestedObject("thresh");
  thr["t12"] = g_th_12; thr["t9"] = g_th_9; thr["t6"] = g_th_6; thr["t3"] = g_th_3; thr["t0"] = g_th_0; thr["hys"] = g_hys; thr["hys_ab"] = g_hys_ab;

  serializeJson(doc, SerialPi);
  SerialPi.print('\n');
  // Also mirror to USB CDC for hosts connected via USB
  serializeJson(doc, Serial);
  Serial.print('\n');
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

static bool auto_calibrate_thresholds(uint32_t settle_ms = 150) {
  // Save mode and PWM settings
  OpMode prev_mode = g_mode;
  bool prev_en = g_pwm_enabled;
  uint16_t prev_duty = g_pwm_duty_pct;
  // Force LINE HIGH (idle) to capture +12V plateau (scaled by divider)
  g_mode = OpMode::MANUAL;
  g_pwm_enabled = false; // apply_pwm_manual() will drive 100% duty (line high)
  apply_pwm_manual();
  uint32_t t0 = millis();
  while (millis() - t0 < settle_ms) { delay(1); }
  // Take multiple bursts and average the robust plateaus
  const int bursts = 6;
  int64_t acc = 0;
  int valid = 0;
  for (int i = 0; i < bursts; ++i) {
    int smin=0, smax=0, savg=0;
    read_cp_mv_stats(smin, smax, savg);
    if (smax > 0) { acc += smax; valid++; }
    delay(5);
  }
  // Restore previous mode and settings
  g_mode = prev_mode;
  g_pwm_enabled = prev_en;
  g_pwm_duty_pct = prev_duty;
  if (prev_mode == OpMode::MANUAL) apply_pwm_manual(); else apply_dc_auto_output(g_last_cp_state);

  if (valid == 0) return false;
  int v12 = (int)(acc / valid);
  // Guard: only allow auto-cal when line is truly at +12V (state A).
  // If an EV is connected (state B/C), the CP positive plateau is ~9V scaled
  // and auto-cal would produce too-low thresholds, misclassifying B as A.
  if (v12 < 2800) { // tuned for this hardware's scaling (~3.0V @ A)
    Serial.print("["); Serial.print(millis()); Serial.print("] [W] auto_cal aborted: v12=");
    Serial.print(v12); Serial.println(" mV (expect ~3000 mV in state A)");
    return false;
  }
  // Compute boundaries between states at the J1772 midpoints:
  // A/B: 10.5V, B/C: 7.5V, C/D: 4.5V, D/E: 1.5V (all relative to 12V reference)
  auto scale = [&](int num, int den) -> int { return (int)((int64_t)v12 * num / den); };
  g_th_12 = scale(105, 120);  // 10.5/12 * V12
  g_th_9  = scale(75, 120);   // 7.5/12 * V12
  g_th_6  = scale(45, 120);   // 4.5/12 * V12
  g_th_3  = scale(15, 120);   // 1.5/12 * V12
  // For E/F boundary we keep existing g_th_0; it's close to 0V; retain hysteresis
  return true;
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
  else if (scmd == "cp.set_thresholds") {
    JsonObject o = doc.as<JsonObject>();
    if (o.containsKey("t12")) g_th_12 = o["t12"].as<int>();
    if (o.containsKey("t9"))  g_th_9  = o["t9"].as<int>();
    if (o.containsKey("t6"))  g_th_6  = o["t6"].as<int>();
    if (o.containsKey("t3"))  g_th_3  = o["t3"].as<int>();
    if (o.containsKey("t0"))  g_th_0  = o["t0"].as<int>();
    if (o.containsKey("hys")) g_hys   = max(0, o["hys"].as<int>());
    if (o.containsKey("hys_ab")) g_hys_ab = max(0, o["hys_ab"].as<int>());
    Serial.print("["); Serial.print(millis()); Serial.print("] [I] thresholds updated: ");
    Serial.print(g_th_12); Serial.print(","); Serial.print(g_th_9); Serial.print(","); Serial.print(g_th_6); Serial.print(","); Serial.print(g_th_3); Serial.print(","); Serial.print(g_th_0); Serial.print(" hys="); Serial.print(g_hys); Serial.print(" hys_ab="); Serial.println(g_hys_ab);
    send_status_json();
  }
  else if (scmd == "cp.scan") {
    StaticJsonDocument<384> out;
    out["type"] = "res";
    out["cmd"] = "cp.scan";
    JsonObject mv = out.createNestedObject("mv");
    const int pins[] = {1,2,3,4,5,6,7,8,9,10};
    for (size_t i = 0; i < sizeof(pins)/sizeof(pins[0]); ++i) {
      int p = pins[i];
      int v = analogReadMilliVolts(p);
      mv[String(p)] = v; // ensure key lifetime is managed by JsonDocument
    }
    serializeJson(out, SerialPi); SerialPi.print('\n');
    serializeJson(out, Serial); Serial.print('\n');
  }
  else if (scmd == "cp.auto_cal") {
    bool ok = auto_calibrate_thresholds();
    StaticJsonDocument<192> resp;
    resp["type"] = ok ? "ok" : "error";
    if (!ok) resp["msg"] = "cal_failed";
    serializeJson(resp, SerialPi); SerialPi.print('\n');
    send_status_json();
  }
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
  // Turn off radios early to minimize ADC jitter
  disable_radios();
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
    const int mv_hist = robust_max_mv();
    // Use current-burst robust plateau for decisions; keep history for smoothing/telemetry
    const int mv = smax;
    const char prev = g_last_cp_state;
    const char cand = cp_state_with_hysteresis(mv, prev);
    // Treat sudden very-low max (missed plateau) as transient if previously connected
    bool transient_low = is_connected_state(prev) && (smax < (g_th_0 - 150));
    // Debounce: stronger confirmation around boundaries
    const uint8_t confirm_needed = mv_strong_in_state(mv, cand) ? 2 : 4;
    // Treat brief upward blips to 'A' while connected as noise unless far above A/B
    const bool a_blip = is_connected_state(prev) && (cand == 'A') && (mv < (g_th_12 + g_hys + 150));
    if (!transient_low && !a_blip) {
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
    g_last_cp_mv = mv;             // publish robust plateau of current burst
    g_last_cp_mv_robust = mv_hist; // history-based smoothing
    g_last_cp_mv_min = smin;
    g_last_cp_mv_avg = savg;
    // Event: CP state transition
    if (st != prev) {
      Serial.print("["); Serial.print(now); Serial.print("] [I] CP state ");
      Serial.print(prev); Serial.print(" -> "); Serial.print(st);
      Serial.print(" at "); Serial.print(mv); Serial.print(" mV (robust="); Serial.print(mv_hist); Serial.println(" mV)");
    }
    // Report after applying (mirror to both Pi UART and USB CDC)
    StaticJsonDocument<256> doc;
    doc["type"] = "status";
    doc["cp_mv"] = mv;
    doc["cp_mv_robust"] = mv_hist;
    doc["state"] = String(st);
    doc["mode"] = (g_mode == OpMode::DC_AUTO) ? "dc" : "manual";
    JsonObject pwm = doc.createNestedObject("pwm");
    pwm["enabled"] = g_pwm_enabled;
    pwm["duty"] = g_pwm_duty_pct;
    pwm["hz"] = g_pwm_freq_hz;
    pwm["out"] = g_last_output_duty_pct; // effective output duty percentage
    JsonObject thr = doc.createNestedObject("thresh");
    thr["t12"] = g_th_12; thr["t9"] = g_th_9; thr["t6"] = g_th_6; thr["t3"] = g_th_3; thr["t0"] = g_th_0; thr["hys"] = g_hys;
    serializeJson(doc, SerialPi);
    SerialPi.print('\n');
    serializeJson(doc, Serial);
    Serial.print('\n');
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
  static String line_uart;
  static String line_usb;
  // UART1 (to Pi or external adapter)
  while (SerialPi.available() > 0) {
    char c = (char)SerialPi.read();
    if (c == '\n') {
      if (line_uart.length() > 0) { process_line(line_uart); line_uart = ""; }
    } else if (c != '\r') {
      if (line_uart.length() < 240) line_uart += c; else line_uart = "";
    }
  }
  // USB CDC (Serial over USB)
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (line_usb.length() > 0) { process_line(line_usb); line_usb = ""; }
    } else if (c != '\r') {
      if (line_usb.length() < 240) line_usb += c; else line_usb = "";
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
