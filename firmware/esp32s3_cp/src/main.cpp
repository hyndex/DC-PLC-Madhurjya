// ESP32-S3 Control Pilot helper
// Old-API compatible, ADC-only with ring-buffer MAX + 5%-aware estimator + B-stickiness
// Board: ESP32-S3-DevKitC-1 (N8R2)

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include "esp_wifi.h"
#include "esp_bt.h"
#include <math.h>
#include <SPI.h>
#include <mcp2515.h>

// ===== PWM (LEDC) =====
#define CP_1_PWM_PIN        38
#define CP_1_PWM_CHANNEL    0
#define CP_1_PWM_FREQUENCY  1000
#define CP_1_PWM_RESOLUTION 12
#define CP_1_MAX_DUTY_CYCLE 4095

// ===== CP ADC =====
#define CP_1_READ_PIN       1

// ===== Threshold anchors (runtime; old API expects t12..t0) =====
// A↔B boundary
static int g_t12 = 2440;   // mid((~2620), (~2260))

// B↔C boundary
static int g_t9  = 2080;   // mid((~2260), (~1900))

// Keep the old step-based API but set the step so t6,t3 align with C/D & below
#ifndef TH_STEP_MV
#define TH_STEP_MV 380      // g_t6 ≈ 1700, g_t3 ≈ 1320, g_t0 ≈ 940
#endif

static int g_t6  = (g_t9 - TH_STEP_MV);  // ≈1700  (C↔D boundary)
static int g_t3  = (g_t6 - TH_STEP_MV);  // ≈1320  (D↔E guard, rarely used in DC)
static int g_t0  = (g_t3 - TH_STEP_MV);  // ≈ 940  (floor)

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

// ===== Peripheral JSON-RPC state (SIM vs HW) =====
struct Meter { float v; float i; float p; float e; };
enum ModePeriph { MODE_SIM = 0, MODE_HW = 1 };
static ModePeriph g_periph_mode = MODE_SIM;

// Contactor I/O (defaults are safe; override at build-time if needed)
#ifndef CONTACTOR_COIL_PIN
#define CONTACTOR_COIL_PIN 7
#endif
#ifndef CONTACTOR_COIL_ACTIVE_HIGH
#define CONTACTOR_COIL_ACTIVE_HIGH 1
#endif
#ifndef CONTACTOR_AUX_PIN
#define CONTACTOR_AUX_PIN -1   // -1 means no AUX wire; use command echo
#endif
#ifndef CONTACTOR_AUX_ACTIVE_HIGH
#define CONTACTOR_AUX_ACTIVE_HIGH 1
#endif

// Internal-linkage variable used in AUX fallback
static bool g_contactor_cmd = false;

static inline void hw_contactor_setup() {
  pinMode(CONTACTOR_COIL_PIN, OUTPUT);
  // Default to OFF (open)
  digitalWrite(CONTACTOR_COIL_PIN, CONTACTOR_COIL_ACTIVE_HIGH ? LOW : HIGH);
#if CONTACTOR_AUX_PIN >= 0
  pinMode(CONTACTOR_AUX_PIN, INPUT);
#endif
}
static inline void hw_contactor_set(bool on) {
  digitalWrite(CONTACTOR_COIL_PIN,
               on ? (CONTACTOR_COIL_ACTIVE_HIGH ? HIGH : LOW)
                  : (CONTACTOR_COIL_ACTIVE_HIGH ? LOW  : HIGH));
}
static inline bool hw_contactor_aux() {
#if CONTACTOR_AUX_PIN >= 0
  int v = digitalRead(CONTACTOR_AUX_PIN);
  return CONTACTOR_AUX_ACTIVE_HIGH ? (v == HIGH) : (v == LOW);
#else
  // Without AUX input, assume aux follows cmd after a short delay
  return g_contactor_cmd;
#endif
}

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

// ===== NEW: MCP2515 + Maxwell ENR (DC module control over CAN) =====
// Pin macros default via build_flags; safe fallbacks here
#ifndef CAN_CS_PIN
#define CAN_CS_PIN  10
#endif
#ifndef CAN_RST_PIN
#define CAN_RST_PIN -1
#endif
#ifndef CAN_INT_PIN
#define CAN_INT_PIN -1
#endif

// Maxwell protocol constants
static const uint8_t  MAXWELL_PROTO = 0x1;   // bits 28:25
static const uint8_t  MAXWELL_MONITOR_ADDR = 0x1;  // bits 24:21 (host addr)
static const uint8_t  MAXWELL_GROUP_DEFAULT = 0x1; // Byte0[7:4]
static const uint32_t CAN_ID_MASK_ALL = 0x1FFFFFFFUL;

static MCP2515 g_mcp2515(CAN_CS_PIN);
bool g_can_ok = false;

// DC targets / ramps
bool  g_dc_enabled = false;
float g_dc_v_target_V = 0.0f;
float g_dc_i_target_A = 0.0f;
float g_dc_v_set_V    = 0.0f;
float g_dc_i_set_A    = 0.0f;

#ifndef DC_V_RAMP_V_PER_S
#define DC_V_RAMP_V_PER_S 50.0f
#endif
#ifndef DC_I_RAMP_A_PER_S
#define DC_I_RAMP_A_PER_S 20.0f
#endif
#ifndef DC_RAMP_TICK_MS
#define DC_RAMP_TICK_MS 100
#endif

#ifndef MAX_MODULES
#define MAX_MODULES 8
#endif
struct MaxwellModule {
  uint8_t  addr;
  uint64_t sn48_9;
  uint32_t last_status;
  uint32_t last_v_mv;
  uint32_t last_i_ma;
  uint32_t last_seen_ms;
};
static MaxwellModule g_modules[MAX_MODULES];
static uint8_t g_module_count = 0;
static uint8_t g_group_addr = MAXWELL_GROUP_DEFAULT;

static inline uint32_t build_maxwell_can_id(uint8_t monitor, uint8_t module, uint8_t prodDay=0, uint16_t snLow9=0) {
  uint32_t id = 0;
  id |= ((uint32_t)(MAXWELL_PROTO & 0x0F) << 25);
  id |= ((uint32_t)(monitor & 0x0F) << 21);
  id |= ((uint32_t)(module  & 0x7F) << 14);
  id |= ((uint32_t)(prodDay & 0x1F) <<  9);
  id |= ((uint32_t)(snLow9  & 0x1FF)     );
  return id;
}
static inline uint8_t b0_group_type(uint8_t group, uint8_t msgType) {
  return (uint8_t)(((group & 0x0F) << 4) | (msgType & 0x0F));
}
static inline void be_put_u32(uint8_t* p, uint32_t v) { p[0]=(uint8_t)(v>>24); p[1]=(uint8_t)(v>>16); p[2]=(uint8_t)(v>>8); p[3]=(uint8_t)v; }
static inline void be_put_u16(uint8_t* p, uint16_t v) { p[0]=(uint8_t)(v>>8);  p[1]=(uint8_t)v; }

static bool maxwell_send(uint32_t id, const uint8_t* data, uint8_t len) {
  if (!g_can_ok) return false;
  struct can_frame f;
  f.can_id  = (id & CAN_ID_MASK_ALL) | CAN_EFF_FLAG;
  f.can_dlc = len;
  for (uint8_t i=0;i<len && i<8;i++) f.data[i] = data[i];
  return (g_mcp2515.sendMessage(&f) == MCP2515::ERROR_OK);
}

static bool cmd_set_vref_mv(uint8_t moduleAddr, uint32_t mv) {
  uint8_t d[8] = { b0_group_type(g_group_addr, 0x0), 0x02, 0,0, 0,0,0,0 };
  be_put_u32(&d[4], mv);
  return maxwell_send(build_maxwell_can_id(MAXWELL_MONITOR_ADDR, moduleAddr), d, 8);
}
static bool cmd_set_ilim_ma(uint8_t moduleAddr, uint32_t ma) {
  uint8_t d[8] = { b0_group_type(g_group_addr, 0x0), 0x03, 0,0, 0,0,0,0 };
  be_put_u32(&d[4], ma);
  return maxwell_send(build_maxwell_can_id(MAXWELL_MONITOR_ADDR, moduleAddr), d, 8);
}
static bool cmd_onoff(uint8_t moduleAddr, bool on) {
  uint8_t d[8] = { b0_group_type(g_group_addr, 0x0), 0x04, 0,0, 0,0,0,0 };
  be_put_u32(&d[4], on ? 0 : 1);
  return maxwell_send(build_maxwell_can_id(MAXWELL_MONITOR_ADDR, moduleAddr), d, 8);
}
static bool cmd_read(uint8_t moduleAddr, uint8_t what) {
  uint8_t d[8] = { b0_group_type(g_group_addr, 0x2), what, 0,0,0,0,0,0 };
  return maxwell_send(build_maxwell_can_id(MAXWELL_MONITOR_ADDR, moduleAddr), d, 8);
}
static bool cmd_allset(uint8_t moduleAddr, uint8_t onoff_hilo, uint16_t i_0p1A, uint16_t vbat_0p1V, uint16_t vout_0p1V) {
  uint8_t d[8] = { b0_group_type(g_group_addr, 0x0B), onoff_hilo, 0,0, 0,0, 0,0 };
  be_put_u16(&d[2], i_0p1A);
  be_put_u16(&d[4], vbat_0p1V);
  be_put_u16(&d[6], vout_0p1V);
  return maxwell_send(build_maxwell_can_id(MAXWELL_MONITOR_ADDR, moduleAddr), d, 8);
}
static bool cmd_set_hilo(uint8_t moduleAddr, uint8_t hilo) {
  uint8_t d[8] = { b0_group_type(g_group_addr, 0x0), 0x5F, 0,0, 0,0,0,0 };
  be_put_u32(&d[4], (uint32_t)hilo);
  return maxwell_send(build_maxwell_can_id(MAXWELL_MONITOR_ADDR, moduleAddr), d, 8);
}

bool can_setup_mcp2515() {
  g_mcp2515.reset();
#if MCP2515_CLK_MHZ == 8
  if (g_mcp2515.setBitrate(CAN_125KBPS, MCP_8MHZ) != MCP2515::ERROR_OK) return false;
#else
  if (g_mcp2515.setBitrate(CAN_125KBPS, MCP_16MHZ) != MCP2515::ERROR_OK) return false;
#endif
  g_mcp2515.setFilterMask(MCP2515::MASK0, true, 0x00000000);
  g_mcp2515.setFilterMask(MCP2515::MASK1, true, 0x00000000);
  g_mcp2515.setNormalMode();
  return true;
}

static void modules_upsert(uint8_t addr) {
  for (uint8_t i=0;i<g_module_count;i++) if (g_modules[i].addr==addr) { g_modules[i].last_seen_ms = millis(); return; }
  if (g_module_count < MAX_MODULES) {
    g_modules[g_module_count] = MaxwellModule{addr, 0, 0, 0, 0, millis()};
    g_module_count++;
  }
}
static void handle_can_frame(const struct can_frame& f) {
  const uint32_t id = f.can_id & CAN_ID_MASK_ALL;
  const uint8_t  proto   = (id >> 25) & 0x0F;
  const uint8_t  modAddr = (id >> 14) & 0x7F;
  if (proto != MAXWELL_PROTO) return;
  if (modAddr==0) return;
  modules_upsert(modAddr);
  const uint8_t b0 = f.data[0];
  const uint8_t msgType = (b0 & 0x0F);
  if (msgType==0x03) { // Read Data Response
    const uint8_t cmd = f.data[1];
    if (cmd==0x00 && f.can_dlc>=8) {
      uint32_t mv = (uint32_t)f.data[4]<<24 | (uint32_t)f.data[5]<<16 | (uint32_t)f.data[6]<<8 | f.data[7];
      for (uint8_t i=0;i<g_module_count;i++) if (g_modules[i].addr==modAddr){ g_modules[i].last_v_mv = mv; }
    } else if (cmd==0x01 && f.can_dlc>=8) {
      uint32_t ma = (uint32_t)f.data[4]<<24 | (uint32_t)f.data[5]<<16 | (uint32_t)f.data[6]<<8 | f.data[7];
      for (uint8_t i=0;i<g_module_count;i++) if (g_modules[i].addr==modAddr){ g_modules[i].last_i_ma = ma; }
    } else if (cmd==0x08 && f.can_dlc>=8) {
      uint32_t st = (uint32_t)f.data[4]<<24 | (uint32_t)f.data[5]<<16 | (uint32_t)f.data[6]<<8 | f.data[7];
      for (uint8_t i=0;i<g_module_count;i++) if (g_modules[i].addr==modAddr){ g_modules[i].last_status = st; }
    }
  }
}

void dc_discover(uint16_t window_ms) {
  g_module_count = 0;
  (void)cmd_read(0x00, 0x08);
  const uint32_t until = millis()+window_ms;
  struct can_frame f;
  while (millis() < until) {
    if (g_mcp2515.readMessage(&f) == MCP2515::ERROR_OK) handle_can_frame(f);
  }
}

static void dc_apply_setpoints_broadcast(bool turnOnOffOnly) {
  uint8_t onoff_hilo = 0x00; // On(DC), no Hi/Lo selection
  const uint16_t i_0p1A   = (uint16_t)lroundf(g_dc_i_set_A * 10.0f);
  const uint16_t vbat_0p1 = (uint16_t)lroundf(g_dc_v_set_V * 10.0f);
  const uint16_t vout_0p1 = (uint16_t)lroundf(g_dc_v_set_V * 10.0f);
  (void)cmd_allset(0x00, onoff_hilo, i_0p1A, vbat_0p1, vout_0p1);
  (void)turnOnOffOnly; // reserved for future
}

static uint32_t g_last_dc_ramp_ms = 0;
void dc_ramp_tick() {
  if ((int32_t)(millis()-g_last_dc_ramp_ms) < (int32_t)DC_RAMP_TICK_MS) return;
  g_last_dc_ramp_ms = millis();
  const bool system_ready = is_connected_state(g_last_cp_state) && g_contactor_aux;
  if (!system_ready) g_dc_enabled = false;
  const float dv = DC_V_RAMP_V_PER_S * (DC_RAMP_TICK_MS/1000.0f);
  const float di = DC_I_RAMP_A_PER_S * (DC_RAMP_TICK_MS/1000.0f);
  auto approach = [](float now, float tgt, float step)->float{
    if (now < tgt) return fminf(tgt, now + step);
    if (now > tgt) return fmaxf(tgt, now - step);
    return now;
  };
  const float tgtV = g_dc_enabled ? g_dc_v_target_V : 0.0f;
  const float tgtI = g_dc_enabled ? g_dc_i_target_A : 0.0f;
  const float prevV = g_dc_v_set_V;
  const float prevI = g_dc_i_set_A;
  g_dc_v_set_V = approach(g_dc_v_set_V, tgtV, dv);
  g_dc_i_set_A = approach(g_dc_i_set_A, tgtI, di);
  if (fabsf(g_dc_v_set_V - prevV) > 0.01f || fabsf(g_dc_i_set_A - prevI) > 0.01f) {
    dc_apply_setpoints_broadcast(false);
  }
}

void dc_emergency_stop() {
  (void)cmd_onoff(0x00, false);
  g_contactor_cmd = false; g_contactor_aux = false;
  if (g_periph_mode==MODE_HW) hw_contactor_set(false);
  g_dc_enabled = false; g_dc_v_target_V = 0; g_dc_i_target_A = 0;
}

static uint32_t g_last_dc_poll_ms = 0;
void dc_poll_tick() {
  const uint32_t now = millis();
  if ((int32_t)(now - g_last_dc_poll_ms) > 300) {
    g_last_dc_poll_ms = now;
    (void)cmd_read(0x00, 0x00);
    (void)cmd_read(0x00, 0x01);
    (void)cmd_read(0x00, 0x08);
  }
  struct can_frame f;
  while (g_mcp2515.readMessage(&f) == MCP2515::ERROR_OK) handle_can_frame(f);
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
    // Preserve JSON-RPC id as-is (string or number)
    JsonVariant idv = doc["id"]; const char* method = doc["method"] | "";
    auto send_res = [&](JsonVariant res, JsonVariant errv = JsonVariant()){
      StaticJsonDocument<512> out; out["type"]="res"; out["id"]=idv; out["ts"]=millis();
      if (errv.isNull()) out["result"]=res; else out["error"]=errv;
      // Mirror responses to both SerialPi (UART1) and USB CDC Serial for host tools
      serializeJson(out, SerialPi); SerialPi.print('\n');
      serializeJson(out, Serial);   Serial.print('\n');
    };
    if (!method[0]) { StaticJsonDocument<128> e; e["code"]=-32600; e["message"]="invalid_request"; send_res(JsonObject(), e); return; }

    if (!strcmp(method,"sys.ping")) {
      g_last_ping_ms = millis();
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
      StaticJsonDocument<256> res; res["commanded"]=g_contactor_cmd;
      bool aux_now = (g_periph_mode==MODE_HW) ? hw_contactor_aux() : (g_contactor_aux);
      bool aux_ok = (aux_now == g_contactor_cmd);
      res["aux_ok"]=aux_ok; res["aux_now"]=aux_now; res["coil_ma"]= g_contactor_cmd ? 120.0 : 0.0; res["reason"]= aux_ok?"ok":"mismatch"; send_res(res); return;
    }
    if (!strcmp(method,"contactor.set")) {
      if ((int32_t)(millis()-g_armed_until_ms) > 0) { StaticJsonDocument<128> e; e["code"]=1001; e["message"]="not_armed"; send_res(JsonObject(), e); return; }
      bool on = doc["params"]["on"] | false; g_contactor_cmd = on;
      if (g_periph_mode==MODE_HW) {
        hw_contactor_set(on);
        delay(50);
        g_contactor_aux = hw_contactor_aux();
      } else {
        delay(40); g_contactor_aux = on; delay(60);
      }
      bool aux_ok=(g_contactor_aux==g_contactor_cmd);
      if (!aux_ok && on) {
        if (g_periph_mode==MODE_HW) hw_contactor_set(false);
        g_contactor_cmd=false; g_contactor_aux=false; StaticJsonDocument<128> e; e["code"]=1002; e["message"]="aux_mismatch"; send_res(JsonObject(), e); return; }
      StaticJsonDocument<128> res; res["ok"]=true; res["aux_ok"]=aux_ok; res["took_ms"]=60; send_res(res); return;
    }
    // --- DC module control (Maxwell over CAN) ---
    if (!strcmp(method,"dc.discover")) {
      dc_discover(250);
      StaticJsonDocument<384> res;
      res["count"] = g_module_count;
      JsonArray arr = res.createNestedArray("mods");
      for (uint8_t i=0;i<g_module_count;i++){ JsonObject m=arr.createNestedObject(); m["addr"]=g_modules[i].addr; m["status"]=g_modules[i].last_status; }
      send_res(res); return;
    }
    if (!strcmp(method,"dc.enable")) {
      bool on = doc["params"]["on"] | false;
      if (on && !g_contactor_aux) {
        if ((int32_t)(millis()-g_armed_until_ms) > 0) { StaticJsonDocument<96> e; e["code"]=1001; e["message"]="not_armed"; send_res(JsonObject(), e); return; }
        g_contactor_cmd = true;
        if (g_periph_mode==MODE_HW) { hw_contactor_set(true); delay(50); g_contactor_aux = hw_contactor_aux(); }
        else { delay(40); g_contactor_aux = true; }
      }
      g_dc_enabled = on;
      dc_apply_setpoints_broadcast(true);
      StaticJsonDocument<128> res; res["enabled"]=g_dc_enabled; res["contactor"]=g_contactor_aux; send_res(res); return;
    }
    if (!strcmp(method,"dc.set")) {
      float vs = doc["params"]["v"] | NAN;   // volts
      float is = doc["params"]["i"] | NAN;   // amps
      if (!isnan(vs)) { if (vs < 0) vs = 0; g_dc_v_target_V = vs; }
      if (!isnan(is)) { if (is < 0) is = 0; g_dc_i_target_A = is; }
      StaticJsonDocument<192> res; res["ok"]=true; res["v_target"]=g_dc_v_target_V; res["i_target"]=g_dc_i_target_A; res["v_set"]=g_dc_v_set_V; res["i_set"]=g_dc_i_set_A; send_res(res); return;
    }
    if (!strcmp(method,"dc.status")) {
      StaticJsonDocument<512> res;
      res["enabled"]=g_dc_enabled;
      res["v_set"]=g_dc_v_set_V; res["i_set"]=g_dc_i_set_A;
      res["mods"]=g_module_count;
      JsonArray arr = res.createNestedArray("tele");
      for (uint8_t i=0;i<g_module_count;i++){
        JsonObject m = arr.createNestedObject();
        m["addr"]=g_modules[i].addr;
        m["v_mv"]=g_modules[i].last_v_mv;
        m["i_ma"]=g_modules[i].last_i_ma;
        m["st"]=g_modules[i].last_status;
      }
      send_res(res); return;
    }
    if (!strcmp(method,"dc.estop")) {
      dc_emergency_stop();
      StaticJsonDocument<96> res; res["ok"]=true; send_res(res); return;
    }
    if (!strcmp(method,"dc.set_hilo")) {
      uint8_t mode = doc["params"]["mode"] | 3; // 1=Hi,2=Lo,3=Auto
      (void)cmd_set_hilo(0x00, mode);
      StaticJsonDocument<96> res; res["ok"]=true; res["mode"]=mode; send_res(res); return;
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
    serializeJson(resp, SerialPi); SerialPi.print('\n'); serializeJson(resp, Serial); Serial.print('\n'); send_status_json();
  }
  else if (scmd=="get_status") { send_status_json(); }
  else if (scmd=="ping")       { StaticJsonDocument<64> resp; resp["type"]="pong"; serializeJson(resp, SerialPi); SerialPi.print('\n'); serializeJson(resp, Serial); Serial.print('\n'); }
  else if (scmd=="restart_slac_hint") {
    uint32_t ms = doc["ms"] | 400; if (ms<50) ms=50; if (ms>2000) ms=2000;
    OpMode prev = g_mode; g_mode=OpMode::MANUAL; g_pwm_enabled=true; g_pwm_duty_pct=100; apply_pwm_manual();
    delay(ms);
    g_mode=OpMode::DC_AUTO; apply_dc_auto_output(g_last_cp_state);
    StaticJsonDocument<96> resp; resp["type"]="ok"; resp["cmd"]="restart_slac_hint"; serializeJson(resp, SerialPi); SerialPi.print('\n'); serializeJson(resp, Serial); Serial.print('\n'); send_status_json(); (void)prev;
  }
  else if (scmd=="reset") {
    StaticJsonDocument<64> resp; resp["type"]="ok"; resp["cmd"]="reset"; serializeJson(resp, SerialPi); SerialPi.print('\n'); serializeJson(resp, Serial); Serial.print('\n');
    delay(50); ESP.restart();
  }
  else {
    StaticJsonDocument<96> resp; resp["type"]="error"; resp["msg"]="unknown_cmd"; serializeJson(resp, SerialPi); SerialPi.print('\n'); serializeJson(resp, Serial); Serial.print('\n');
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

  // Initialize contactor I/O (safe defaults)
  hw_contactor_setup();

  Serial.println("Init done.");

  // ===== CAN (MCP2515) bring-up =====
  // Optional reset pin
#ifdef CAN_RST_PIN
  #if (CAN_RST_PIN >= 0)
    pinMode(CAN_RST_PIN, OUTPUT);
    digitalWrite(CAN_RST_PIN, LOW);
    delay(5);
    digitalWrite(CAN_RST_PIN, HIGH);
    delay(5);
  #endif
#endif
  // SPI wiring: use custom pins if provided via build flags
#ifdef CAN_SCK_PIN
  #if (CAN_SCK_PIN >= 0) && (CAN_MOSI_PIN >= 0) && (CAN_MISO_PIN >= 0)
    SPI.begin(CAN_SCK_PIN, CAN_MISO_PIN, CAN_MOSI_PIN);
  #else
    SPI.begin();
  #endif
#else
  SPI.begin();
#endif
  // Guard INT pin usage
#ifndef CAN_INT_PIN
  #define CAN_INT_PIN -1
#endif
  if (CAN_INT_PIN >= 0) pinMode(CAN_INT_PIN, INPUT_PULLUP);

  // Set bitrate and mode
  extern bool can_setup_mcp2515();
  extern bool g_can_ok;
  if (can_setup_mcp2515()) {
    g_can_ok = true;
    Serial.println("[CAN] MCP2515 ready @125kbps (extended)");
    extern void dc_discover(uint16_t);
    dc_discover(200);
  } else {
    Serial.println("[CAN] MCP2515 init FAILED!");
  }
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
      // Mirror events to both SerialPi and USB CDC Serial
      serializeJson(evt, SerialPi); SerialPi.print('\n');
      serializeJson(evt, Serial);   Serial.print('\n');
    }
    if (g_temps_stream) {
      StaticJsonDocument<192> pld; pld.createNestedObject("gun_a")["c"] = 32.0 + (g_contactor_aux?12.0:0.5);
      pld.createNestedObject("gun_b")["c"] = 31.5 + (g_contactor_aux?11.0:0.3);
      StaticJsonDocument<256> evt; evt["type"]="evt"; evt["ts"]=now; evt["id"]=0; evt["method"]="evt:temps.tick"; evt["result"]=pld;
      // Mirror events to both SerialPi and USB CDC Serial
      serializeJson(evt, SerialPi); SerialPi.print('\n');
      serializeJson(evt, Serial);   Serial.print('\n');
    }
  }

  // === Contactor keepalive failsafe (unchanged) ===
  if ((now - g_last_ping_ms) > 6000 && g_contactor_cmd) {
    g_contactor_cmd = false; g_contactor_aux = false;
    if (g_periph_mode==MODE_HW) hw_contactor_set(false);
    StaticJsonDocument<96> evt; evt["type"]="evt"; evt["ts"]=now; evt["id"]=0; evt["method"]="evt:failsafe.keepalive";
    JsonObject res = evt.createNestedObject("result"); res["forced"]="contactor_off";
    // Mirror events to both SerialPi and USB CDC Serial
    serializeJson(evt, SerialPi); SerialPi.print('\n');
    serializeJson(evt, Serial);   Serial.print('\n');
  }

  // === DC CAN integration ===
  extern bool g_can_ok;
  if (g_can_ok) {
    extern void dc_ramp_tick();
    extern void dc_poll_tick();
    dc_ramp_tick();
    dc_poll_tick();
  }
}
