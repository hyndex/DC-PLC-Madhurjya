// ESP32-S3 Control Pilot helper
// Old-API compatible, ADC-only with ring-buffer MAX + 5%-aware estimator + B-stickiness
// Board: ESP32-S3-DevKitC-1 (N8R2)

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include "esp_wifi.h"
#include "esp_bt.h"

// ===== PWM (LEDC) =====
#define CP_1_PWM_PIN        38
#define CP_1_PWM_CHANNEL    0
#define CP_1_PWM_FREQUENCY  1000
#define CP_1_PWM_RESOLUTION 12
#define CP_1_MAX_DUTY_CYCLE 4095

// ===== CP ADC =====
#define CP_1_READ_PIN       1

// ===== Threshold anchors (runtime; old API expects t12..t0) =====
static int g_t12 = 2300; // A boundary
static int g_t9  = 2100; // B boundary
#ifndef TH_STEP_MV
#define TH_STEP_MV 300
#endif
static int g_t6  = (g_t9 - TH_STEP_MV);
static int g_t3  = (g_t6 - TH_STEP_MV);
static int g_t0  = (g_t3 - TH_STEP_MV);

// Hysteresis placeholders (kept in JSON; not used)
static int g_hys    = 0;
static int g_hys_ab = 0;

// ===== Timing =====
#ifndef MEAS_PERIOD_MS
#define MEAS_PERIOD_MS 20        // ~50 Hz decisions
#endif
#ifndef STATUS_PERIOD_MS
#define STATUS_PERIOD_MS 200
#endif
#ifndef USB_LOG_PERIOD_MS
#define USB_LOG_PERIOD_MS 1000
#endif

// ===== Sampling (robust plateau suited for 5% duty) =====
#ifndef SAMPLE_COUNT
#define SAMPLE_COUNT 384         // denser than 256 to reduce "all-miss" bursts
#endif
#ifndef SAMPLE_DELAY_US
#define SAMPLE_DELAY_US 6
#endif
#ifndef TOPK
#define TOPK 48                  // larger Top-K buffer; upper-sixth trimmed mean
#endif

// ===== Ring buffer (stabilize with window MAX) =====
#ifndef RBUF_LEN
#define RBUF_LEN 24              // ~24*20ms ≈ 480ms memory window
#endif
static int      g_rbuf[RBUF_LEN];
static uint8_t  g_rhead = 0;
static uint8_t  g_rcount = 0;

// ===== B-stickiness (allow demotion only after prolonged drought) =====
#ifndef B_DEMOTE_BURSTS
#define B_DEMOTE_BURSTS 18       // ~18*20ms ≈ 360ms of no ≥t9 evidence to demote B
#endif
static uint16_t g_belowB_run = 0; // consecutive bursts without any ≥t9 observation

// ===== UART to host (unchanged pins) =====
#define ESP_UART_RX 44
#define ESP_UART_TX 43
HardwareSerial SerialPi(1);

// ===== Peripheral JSON-RPC state (unchanged) =====
struct Meter { float v; float i; float p; float e; };
enum ModePeriph { MODE_SIM = 0, MODE_HW = 1 };
static ModePeriph g_periph_mode = MODE_SIM;

enum class OpMode : uint8_t { MANUAL = 0, DC_AUTO = 1 };
static volatile OpMode g_mode = OpMode::DC_AUTO;

static volatile bool     g_pwm_enabled    = false; // manual only
static volatile uint16_t g_pwm_duty_pct   = 0;     // manual only
static volatile uint32_t g_pwm_freq_hz    = CP_1_PWM_FREQUENCY;

// ===== State / Telemetry (compatible names) =====
static uint32_t g_up0_ms = 0;
static uint32_t g_last_ping_ms = 0;

static char     g_last_cp_state = 'A';
static int      g_last_cp_mv = 0;            // last burst plateau (robust)
static int      g_last_cp_mv_peak_in_burst = 0; // raw peak within the burst
static int      g_last_cp_mv_robust = 0;     // ring-buffer MAX (stable representative)
static int      g_last_cp_mv_min = 0;        // telemetry
static int      g_last_cp_mv_avg = 0;        // telemetry
static uint16_t g_last_output_duty_pct = 100;

static bool     g_contactor_cmd = false;
static bool     g_contactor_aux = false;
static uint32_t g_armed_until_ms = 0;
static bool     g_meter_stream = false;
static bool     g_temps_stream = false;

static uint32_t g_last_usb_log_ms = 0;
static uint32_t g_last_status_ms  = 0;
static uint32_t g_last_meas_ms    = 0;

static uint32_t g_sample_phase_us = 0; // de-phase vs 1 kHz PWM

// cache LEDC duty to avoid redundant writes
static uint32_t g_last_ledc_duty = 0xFFFFFFFFu;

// ===== Utils (unchanged) =====
static void disable_radios() {
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
  esp_wifi_stop();
  if (esp_bt_controller_get_status() == ESP_BT_CONTROLLER_STATUS_ENABLED)
    esp_bt_controller_disable();
  esp_bt_controller_mem_release(ESP_BT_MODE_BLE);
}
static inline uint32_t pct_to_duty(uint16_t pct) {
  if (pct == 0) return 0;
  if (pct >= 100) return CP_1_MAX_DUTY_CYCLE;
  return (uint32_t)((CP_1_MAX_DUTY_CYCLE * (uint32_t)pct) / 100U);
}
static inline void write_ledc_duty(uint32_t duty) {
  if (duty != g_last_ledc_duty) { ledcWrite(CP_1_PWM_CHANNEL, duty); g_last_ledc_duty = duty; }
}
static inline void apply_pwm_manual() {
  const uint32_t duty = g_pwm_enabled ? pct_to_duty(g_pwm_duty_pct)
                                      : CP_1_MAX_DUTY_CYCLE;  // idle high
  write_ledc_duty(duty);
}
static void configure_pwm() {
  ledcSetup(CP_1_PWM_CHANNEL, g_pwm_freq_hz, CP_1_PWM_RESOLUTION);
  ledcAttachPin(CP_1_PWM_PIN, CP_1_PWM_CHANNEL);
  if (g_mode == OpMode::MANUAL) apply_pwm_manual();
}
static inline bool is_connected_state(char st) { return (st=='B' || st=='C' || st=='D'); }
static inline void apply_dc_auto_output(char st) {
  // Old behavior: B/C/D => 5% duty; else keep +12V (100%)
  g_last_output_duty_pct = (st=='B' || st=='C' || st=='D') ? 5 : 100;
  write_ledc_duty(pct_to_duty(g_last_output_duty_pct));
}

// ===== Robust plateau (Top-K, 5%-aware upper-sixth trimmed mean) =====
static void read_cp_mv_burst(int &min_mv, int &plateau_mv, int &avg_mv, int &peak_mv) {
  int minv = INT32_MAX, max_seen = INT32_MIN;
  int64_t acc = 0;
  int topk[TOPK]; int tk = 0;

  auto insert_topk = [&](int v){
    if (tk < TOPK) {
      int i = tk++;
      while (i>0 && topk[i-1] > v) { topk[i] = topk[i-1]; --i; }
      topk[i] = v;
    } else if (v > topk[0]) {
      topk[0] = v;
      int i = 0; while (i+1<tk && topk[i] > topk[i+1]) { int t=topk[i]; topk[i]=topk[i+1]; topk[i+1]=t; ++i; }
    }
  };

  if (g_sample_phase_us) delayMicroseconds(g_sample_phase_us);
  (void)analogRead(CP_1_READ_PIN);

  for (int i=0;i<SAMPLE_COUNT;++i) {
    delayMicroseconds(SAMPLE_DELAY_US);
    int v = analogReadMilliVolts(CP_1_READ_PIN);
    acc += v;
    if (v < minv)     minv = v;
    if (v > max_seen) max_seen = v;
    insert_topk(v);
  }

  int robust = 0;
  if (tk == 0) {
    robust = (max_seen==INT32_MIN) ? 0 : max_seen;
  } else {
    // 5%-aware: average the upper ~15–20% and drop one top outlier
    int start = tk - max(3, tk / 6);            // upper sixth
    int end   = tk - (tk >= 6 ? 1 : 0);         // drop very top 1
    if (start < 0) start = 0;
    if (end <= start) { start = (tk>3)?(tk-3):0; end = tk; }
    int64_t s=0; int n=0;
    for (int i=start;i<end;++i){ s += topk[i]; ++n; }
    robust = (n>0) ? (int)(s/n) : topk[tk-1];
  }

  min_mv   = (minv==INT32_MAX)?0:minv;
  plateau_mv = robust;
  avg_mv   = (int)(acc / (int64_t)SAMPLE_COUNT);
  peak_mv  = (max_seen==INT32_MIN)?0:max_seen;

  g_sample_phase_us = (g_sample_phase_us + 53) % 1000; // wander vs 1kHz
}

// ===== Ring buffer helpers =====
static inline int rb_push_and_max(int v) {
  g_rbuf[g_rhead] = v;
  g_rhead = (g_rhead + 1) % RBUF_LEN;
  if (g_rcount < RBUF_LEN) g_rcount++;
  int mx = g_rbuf[0];
  for (uint8_t i=1;i<g_rcount;++i) if (g_rbuf[i] > mx) mx = g_rbuf[i];
  return mx;
}

// ===== Classifier (ADC-only, no hysteresis) =====
static inline char classify_state_from_mv(int mv) {
  if (mv >= g_t12) return 'A';
  if (mv >= g_t9 ) return 'B';
  if (mv >= g_t6 ) return 'C';
  if (mv >= g_t3 ) return 'D';
  if (mv >= g_t0 ) return 'E';
  return 'F';
}

// ===== Status JSON (old shape preserved) =====
static void send_status_json() {
  StaticJsonDocument<256> doc;
  doc["type"] = "status";
  doc["cp_mv"] = g_last_cp_mv;               // last burst robust plateau
  doc["cp_mv_robust"] = g_last_cp_mv_robust; // ring-buffer MAX (stable representative)
  doc["state"] = String(g_last_cp_state);
  doc["mode"]  = (g_mode == OpMode::DC_AUTO) ? "dc" : "manual";
  JsonObject pwm = doc.createNestedObject("pwm");
  pwm["enabled"] = g_pwm_enabled;
  pwm["duty"]    = g_pwm_duty_pct;
  pwm["hz"]      = g_pwm_freq_hz;
  pwm["out"]     = g_last_output_duty_pct;
  JsonObject thr = doc.createNestedObject("thresh");     // keep keys for compatibility
  thr["t12"] = g_t12; thr["t9"] = g_t9; thr["t6"] = g_t6; thr["t3"] = g_t3; thr["t0"] = g_t0;
  thr["hys"] = g_hys; thr["hys_ab"] = g_hys_ab;

  serializeJson(doc, SerialPi); SerialPi.print('\n');
  serializeJson(doc, Serial);   Serial.print('\n');
}

// ===== Old API: command handlers (unchanged behavior) =====
static void handle_cmd_set_pwm(JsonObject obj) {
  if (g_mode != OpMode::MANUAL) {
    StaticJsonDocument<128> resp; resp["type"]="error"; resp["msg"]="mode_dc_auto";
    serializeJson(resp, SerialPi); SerialPi.print('\n'); return;
  }
  if (obj.containsKey("duty")) {
    int d = obj["duty"].as<int>(); if (d < 0) d = 0; if (d > 100) d = 100; g_pwm_duty_pct = (uint16_t)d;
  }
  if (obj.containsKey("enable")) g_pwm_enabled = obj["enable"].as<bool>();
  apply_pwm_manual(); send_status_json();
}
static void handle_cmd_enable_pwm(JsonObject obj) {
  if (g_mode != OpMode::MANUAL) {
    StaticJsonDocument<128> resp; resp["type"]="error"; resp["msg"]="mode_dc_auto";
    serializeJson(resp, SerialPi); SerialPi.print('\n'); return;
  }
  g_pwm_enabled = obj["enable"].as<bool>();
  apply_pwm_manual(); send_status_json();
}
static void handle_cmd_set_freq(JsonObject obj) {
  uint32_t hz = obj["hz"].as<uint32_t>(); if (hz<500) hz=500; if (hz>5000) hz=5000;
  g_pwm_freq_hz = hz; configure_pwm(); send_status_json();
}
static void handle_cmd_set_mode(JsonObject obj) {
  const char* m = obj["mode"] | "";
  if (!strcmp(m,"dc")) g_mode = OpMode::DC_AUTO;
  else if (!strcmp(m,"manual")) g_mode = OpMode::MANUAL;
  else { StaticJsonDocument<96> resp; resp["type"]="error"; resp["msg"]="bad_mode"; serializeJson(resp, SerialPi); SerialPi.print('\n'); return; }
  if (g_mode == OpMode::MANUAL) apply_pwm_manual(); else apply_dc_auto_output(g_last_cp_state);
  send_status_json();
}

// ===== Auto-calibrate thresholds (old API: cp.auto_cal) =====
static bool auto_calibrate_thresholds(uint32_t settle_ms = 150) {
  OpMode prev_mode = g_mode; bool prev_en = g_pwm_enabled; uint16_t prev_duty = g_pwm_duty_pct;
  g_mode = OpMode::MANUAL; g_pwm_enabled = false; apply_pwm_manual();
  uint32_t t0 = millis(); while (millis()-t0 < settle_ms) delay(1);

  const int bursts = 6; int64_t acc=0; int valid=0;
  for (int i=0;i<bursts;++i){
    int smin=0,srob=0,savg=0,spk=0; (void)spk;
    read_cp_mv_burst(smin,srob,savg,spk);
    if (srob>0){acc+=srob; valid++;} delay(5);
  }

  g_mode = prev_mode; g_pwm_enabled = prev_en; g_pwm_duty_pct = prev_duty;
  if (prev_mode == OpMode::MANUAL) apply_pwm_manual(); else apply_dc_auto_output(g_last_cp_state);

  if (!valid) return false;
  int v12 = (int)(acc/valid);
  if (v12 < 2000) return false; // sanity on scaling

  // Midpoints on 12V scale: 10.5V, 7.5V, 4.5V, 1.5V
  auto scale = [&](int num, int den)->int { return (int)((int64_t)v12 * num / den); };
  g_t12 = scale(105,120);
  g_t9  = scale(75,120);
  g_t6  = scale(45,120);
  g_t3  = scale(15,120);
  if (g_t0 > g_t3 - 2*TH_STEP_MV) g_t0 = g_t3 - TH_STEP_MV;
  return true;
}

// ===== RPC / legacy command processing (unchanged endpoints) =====
static void process_line(String &line) {
  StaticJsonDocument<768> doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) {
    StaticJsonDocument<128> resp; resp["type"]="error"; resp["msg"]=String("bad_json:")+err.c_str();
    serializeJson(resp, SerialPi); SerialPi.print('\n'); return;
  }

  // JSON-RPC path
  const char* mtype = doc["type"] | "";
  if (strcmp(mtype, "req") == 0) {
    uint32_t id = doc["id"] | 0; const char* method = doc["method"] | "";
    auto send_res = [&](JsonVariant res, JsonVariant errv = JsonVariant()){
      StaticJsonDocument<512> out; out["type"]="res"; out["id"]=id; out["ts"]=millis();
      if (errv.isNull()) out["result"]=res; else out["error"]=errv;
      serializeJson(out, SerialPi); SerialPi.print('\n');
    };
    if (!method[0]) { StaticJsonDocument<128> e; e["code"]=-32600; e["message"]="invalid_request"; send_res(JsonObject(), e); return; }

    if (!strcmp(method,"sys.ping")) {
      StaticJsonDocument<256> res; res["up_ms"]=millis()-g_up0_ms; res["mode"]=(g_periph_mode==MODE_SIM)?"sim":"hw";
      res.createNestedObject("temps")["mcu"]=temperatureRead(); send_res(res); return;
    }
    if (!strcmp(method,"sys.info")) {
      StaticJsonDocument<384> res; res["fw"]="esp-cp-periph/0.5.0"; res["proto"]=1; res["mode"]=(g_periph_mode==MODE_SIM)?"sim":"hw";
      JsonArray caps = res.createNestedArray("capabilities"); caps.add("cp"); caps.add("contactor"); caps.add("temps.gun_a"); caps.add("temps.gun_b"); caps.add("meter");
      send_res(res); return;
    }
    if (!strcmp(method,"sys.arm")) { g_armed_until_ms = millis() + 1500; StaticJsonDocument<96> res; res["armed_until_ms"]=g_armed_until_ms; send_res(res); return; }
    if (!strcmp(method,"sys.set_mode")) {
      const char* m = doc["params"]["mode"] | "sim"; g_periph_mode = (!strcmp(m,"hw"))? MODE_HW : MODE_SIM;
      StaticJsonDocument<96> res; res["mode"]=(g_periph_mode==MODE_SIM)?"sim":"hw"; send_res(res); return;
    }
    if (!strcmp(method,"contactor.check")) {
      StaticJsonDocument<256> res; res["commanded"]=g_contactor_cmd; bool aux_ok=(g_contactor_aux==g_contactor_cmd);
      res["aux_ok"]=aux_ok; res["coil_ma"]= g_contactor_cmd ? 120.0 : 0.0; res["reason"]= aux_ok?"ok":"mismatch"; send_res(res); return;
    }
    if (!strcmp(method,"contactor.set")) {
      if ((int32_t)(millis()-g_armed_until_ms) > 0) { StaticJsonDocument<128> e; e["code"]=1001; e["message"]="not_armed"; send_res(JsonObject(), e); return; }
      bool on = doc["params"]["on"] | false; g_contactor_cmd = on; delay(40); g_contactor_aux = on; delay(60);
      bool aux_ok=(g_contactor_aux==g_contactor_cmd);
      if (!aux_ok && on) { g_contactor_cmd=false; g_contactor_aux=false; StaticJsonDocument<128> e; e["code"]=1002; e["message"]="aux_mismatch"; send_res(JsonObject(), e); return; }
      StaticJsonDocument<128> res; res["ok"]=true; res["aux_ok"]=aux_ok; res["took_ms"]=60; send_res(res); return;
    }
    if (!strcmp(method,"temps.read")) {
      StaticJsonDocument<256> res; JsonObject t = res.createNestedObject("temps");
      t.createNestedObject("gun_a")["c"]=32.0 + (g_contactor_aux?12.0:0.5);
      t.createNestedObject("gun_b")["c"]=31.5 + (g_contactor_aux?11.0:0.3); send_res(res); return;
    }
    if (!strcmp(method,"meter.read")) {
      static float e=0.0f; float on=g_contactor_aux?1.0f:0.0f; float v=415.0f; float i=on*50.0f; float p=v*i/1000.0f; e += p*0.001f;
      StaticJsonDocument<256> res; res["v"]=v; res["i"]=i; res["p"]=p; res["e"]=e; send_res(res); return;
    }
    if (!strcmp(method,"meter.stream_start")) { g_meter_stream=true;  send_res(JsonObject()); return; }
    if (!strcmp(method,"meter.stream_stop"))  { g_meter_stream=false; send_res(JsonObject()); return; }
    if (!strcmp(method,"temps.stream_start")) { g_temps_stream=true;  send_res(JsonObject()); return; }
    if (!strcmp(method,"temps.stream_stop"))  { g_temps_stream=false; send_res(JsonObject()); return; }

    StaticJsonDocument<128> e; e["code"]=-32601; e["message"]="unknown_method"; send_res(JsonObject(), e); return;
  }

  // Legacy CP command path (strings)
  const char* cmd = doc["cmd"] | "";
  if (!cmd[0]) { StaticJsonDocument<96> resp; resp["type"]="error"; resp["msg"]="missing_cmd"; serializeJson(resp, SerialPi); SerialPi.print('\n'); return; }
  String scmd(cmd);

  if      (scmd=="set_pwm")            { handle_cmd_set_pwm(doc.as<JsonObject>()); }
  else if (scmd=="enable_pwm")         { handle_cmd_enable_pwm(doc.as<JsonObject>()); }
  else if (scmd=="set_freq")           { handle_cmd_set_freq(doc.as<JsonObject>()); }
  else if (scmd=="set_mode")           { handle_cmd_set_mode(doc.as<JsonObject>()); }
  else if (scmd=="cp.set_thresholds") {
    JsonObject o = doc.as<JsonObject>();
    if (o.containsKey("t12")) g_t12 = o["t12"].as<int>();
    if (o.containsKey("t9"))  g_t9  = o["t9"].as<int>();
    if (o.containsKey("t6"))  g_t6  = o["t6"].as<int>();
    if (o.containsKey("t3"))  g_t3  = o["t3"].as<int>();
    if (o.containsKey("t0"))  g_t0  = o["t0"].as<int>();
    if (o.containsKey("hys"))   g_hys   = 0; // accept, but unused
    if (o.containsKey("hys_ab"))g_hys_ab= 0;
    send_status_json();
  }
  else if (scmd=="cp.scan") {
    StaticJsonDocument<384> out; out["type"]="res"; out["cmd"]="cp.scan";
    JsonObject mv = out.createNestedObject("mv");
    const int pins[] = {1,2,3,4,5,6,7,8,9,10};
    for (size_t i=0;i<sizeof(pins)/sizeof(pins[0]); ++i) mv[String(pins[i])] = analogReadMilliVolts(pins[i]);
    serializeJson(out, SerialPi); SerialPi.print('\n'); serializeJson(out, Serial); Serial.print('\n');
  }
  else if (scmd=="cp.auto_cal") {
    bool ok = auto_calibrate_thresholds();
    StaticJsonDocument<192> resp; resp["type"]= ok ? "ok" : "error"; if (!ok) resp["msg"]="cal_failed";
    serializeJson(resp, SerialPi); SerialPi.print('\n'); send_status_json();
  }
  else if (scmd=="get_status") { send_status_json(); }
  else if (scmd=="ping")       { StaticJsonDocument<64> resp; resp["type"]="pong"; serializeJson(resp, SerialPi); SerialPi.print('\n'); }
  else if (scmd=="restart_slac_hint") {
    uint32_t ms = doc["ms"] | 400; if (ms<50) ms=50; if (ms>2000) ms=2000;
    OpMode prev = g_mode; g_mode=OpMode::MANUAL; g_pwm_enabled=true; g_pwm_duty_pct=100; apply_pwm_manual();
    delay(ms);
    g_mode=OpMode::DC_AUTO; apply_dc_auto_output(g_last_cp_state);
    StaticJsonDocument<96> resp; resp["type"]="ok"; resp["cmd"]="restart_slac_hint"; serializeJson(resp, SerialPi); SerialPi.print('\n'); send_status_json(); (void)prev;
  }
  else if (scmd=="reset") {
    StaticJsonDocument<64> resp; resp["type"]="ok"; resp["cmd"]="reset"; serializeJson(resp, SerialPi); SerialPi.print('\n');
    delay(50); ESP.restart();
  }
  else {
    StaticJsonDocument<96> resp; resp["type"]="error"; resp["msg"]="unknown_cmd"; serializeJson(resp, SerialPi); SerialPi.print('\n');
  }
}

// ===== Arduino setup/loop =====
void setup() {
  Serial.begin(115200);
  disable_radios();
  while (!Serial && millis() < 1500) { /* wait USB */ }
  Serial.println("ESP32-S3 CP Helper (old-API compatible, ring-max + 5%-aware + B-stick) booting...");

  SerialPi.begin(115200, SERIAL_8N1, ESP_UART_RX, ESP_UART_TX);
  g_up0_ms = millis();

  pinMode(CP_1_READ_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(CP_1_READ_PIN, ADC_11db);

  ledcSetup(CP_1_PWM_CHANNEL, g_pwm_freq_hz, CP_1_PWM_RESOLUTION);
  ledcAttachPin(CP_1_PWM_PIN, CP_1_PWM_CHANNEL);
  write_ledc_duty(CP_1_MAX_DUTY_CYCLE); // idle high

  for (uint8_t i=0;i<RBUF_LEN;++i) g_rbuf[i]=0;

  Serial.println("Init done.");
}

void loop() {
  const uint32_t now = millis();

  // === FAST MEASUREMENT & DECISION (ADC-only, ring-max) ===
  if ((now - g_last_meas_ms) >= MEAS_PERIOD_MS) {
    g_last_meas_ms = now;

    int smin=0, srob=0, savg=0, spk=0;
    read_cp_mv_burst(smin, srob, savg, spk);

    g_last_cp_mv_min   = smin;
    g_last_cp_mv       = srob;             // last burst robust plateau
    g_last_cp_mv_peak_in_burst = spk;      // burst peak (raw)
    g_last_cp_mv_avg   = savg;

    // Track consecutive bursts that failed to show any ≥t9 evidence
    bool burst_has_B = (spk >= g_t9);      // use burst peak to detect any B-level hit
    g_belowB_run = burst_has_B ? 0 : (uint16_t)min<int>(g_belowB_run + 1, 1000);

    // Ring buffer MAX (window MAX) for stable classification
    g_last_cp_mv_robust = rb_push_and_max(g_last_cp_mv);

    // Tentative by ring MAX
    char tentative = classify_state_from_mv(g_last_cp_mv_robust);
    char new_state = tentative;

    // B-stickiness: hold B unless we've starved ≥t9 for long enough
    if (g_last_cp_state == 'B' && (tentative=='C' || tentative=='D' || tentative=='E' || tentative=='F')) {
      if (g_belowB_run < B_DEMOTE_BURSTS) new_state = 'B';
    }

    if (new_state != g_last_cp_state) {
      char prev = g_last_cp_state;
      g_last_cp_state = new_state;
      if (g_mode == OpMode::DC_AUTO) apply_dc_auto_output(g_last_cp_state);
      else apply_pwm_manual();
      Serial.printf("[%lu] [I] CP state %c -> %c (peakWin=%d, last=%d, burstPk=%d, noB_run=%u)\n",
                    now, prev, g_last_cp_state, g_last_cp_mv_robust, g_last_cp_mv, g_last_cp_mv_peak_in_burst, g_belowB_run);
    } else {
      if (g_mode == OpMode::DC_AUTO) apply_dc_auto_output(g_last_cp_state);
      else apply_pwm_manual();
    }
  }

  // === STATUS EMISSION (unchanged cadence/shape) ===
  if ((now - g_last_status_ms) >= STATUS_PERIOD_MS) {
    g_last_status_ms = now;
    send_status_json();
  }

  // === Logs (unchanged) ===
  if (now - g_last_usb_log_ms >= USB_LOG_PERIOD_MS) {
    g_last_usb_log_ms = now;
    Serial.printf("[%lu] [S] winMAX=%d last=%d burstPk=%d min=%d avg=%d state=%c mode=%s pwm: en=%d duty%%=%u hz=%lu outDuty%%=%u noB_run=%u\n",
                  now, g_last_cp_mv_robust, g_last_cp_mv, g_last_cp_mv_peak_in_burst,
                  g_last_cp_mv_min, g_last_cp_mv_avg, g_last_cp_state,
                  (g_mode==OpMode::DC_AUTO)?"dc":"manual",
                  g_pwm_enabled, g_pwm_duty_pct, (unsigned long)g_pwm_freq_hz, g_last_output_duty_pct,
                  g_belowB_run);
  }

  // === Command RX (unchanged) ===
  static String line_uart, line_usb;
  while (SerialPi.available() > 0) {
    char c = (char)SerialPi.read();
    if (c == '\n') { if (line_uart.length() > 0) { process_line(line_uart); line_uart = ""; } }
    else if (c != '\r') { if (line_uart.length() < 240) line_uart += c; else line_uart = ""; }
  }
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') { if (line_usb.length() > 0) { process_line(line_usb); line_usb = ""; } }
    else if (c != '\r') { if (line_usb.length() < 240) line_usb += c; else line_usb = ""; }
  }

  // === Demo streams (unchanged) ===
  static uint32_t last_periph_tick = 0;
  if (now - last_periph_tick >= 1000) {
    last_periph_tick = now;
    if (g_meter_stream) {
      static float e=0.0f; float on=g_contactor_aux?1.0f:0.0f; float v=415.0f; float i=on*50.0f; float p=v*i/1000.0f; e += p*0.001f;
      StaticJsonDocument<192> pld; pld["v"]=v; pld["i"]=i; pld["p"]=p; pld["e"]=e;
      StaticJsonDocument<256> evt; evt["type"]="evt"; evt["ts"]=now; evt["id"]=0; evt["method"]="evt:meter.tick"; evt["result"]=pld;
      serializeJson(evt, SerialPi); SerialPi.print('\n');
    }
    if (g_temps_stream) {
      StaticJsonDocument<192> pld; pld.createNestedObject("gun_a")["c"] = 32.0 + (g_contactor_aux?12.0:0.5);
      pld.createNestedObject("gun_b")["c"] = 31.5 + (g_contactor_aux?11.0:0.3);
      StaticJsonDocument<256> evt; evt["type"]="evt"; evt["ts"]=now; evt["id"]=0; evt["method"]="evt:temps.tick"; evt["result"]=pld;
      serializeJson(evt, SerialPi); SerialPi.print('\n');
    }
  }

  // === Contactor keepalive failsafe (unchanged) ===
  if ((now - g_last_ping_ms) > 6000 && g_contactor_cmd) {
    g_contactor_cmd = false; g_contactor_aux = false;
    StaticJsonDocument<96> evt; evt["type"]="evt"; evt["ts"]=now; evt["id"]=0; evt["method"]="evt:failsafe.keepalive";
    JsonObject res = evt.createNestedObject("result"); res["forced"]="contactor_off";
    serializeJson(evt, SerialPi); SerialPi.print('\n');
  }
}
