// MaxwellENR_MCP2515.hpp (header-only)
#pragma once
#include <Arduino.h>
#include <SPI.h>

namespace jp {

class MaxwellENR_MCP2515 {
public:
  enum class HiLoMode : uint8_t { HIGH = 1, LOW = 2, AUTO = 3 };

  struct Config {
    int pinSCK  = 7;
    int pinMOSI = 15;
    int pinMISO = 16;
    int pinCS   = 13;
    int pinRST  = 14;
    int pinINT  = 42;   // optional

    uint8_t  mcpOscMHz    = 8;         // 8 or 16
    uint32_t canBitrate   = 125000;    // 125 kbps
    uint8_t  monitorAddr  = 0x01;
    uint8_t  moduleAddr   = 0x01;
    uint8_t  groupNibble  = 0x01;

    uint32_t pMaxW        = 30000;     // 30 kW power cap
    uint32_t hvEnter_mV   = 500000;    // enter HIGH >= 500V
    uint32_t hvExit_mV    = 400000;    // exit HIGH  <= 400V
    uint32_t vMin_mV      = 200000;    // 200 V
    uint32_t vMax_mV      = 1000000;   // 1000 V
  };

  explicit MaxwellENR_MCP2515(const Config& cfg = Config())
  : _cfg(cfg), _spi(FSPI) {}

  bool begin() {
    pinMode(_cfg.pinCS, OUTPUT);   CS_HIGH();
    pinMode(_cfg.pinRST, OUTPUT);  digitalWrite(_cfg.pinRST, HIGH);
    if (_cfg.pinINT >= 0) pinMode(_cfg.pinINT, INPUT_PULLUP);

    _spi.begin(_cfg.pinSCK, _cfg.pinMISO, _cfg.pinMOSI, _cfg.pinCS);
    _spi.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));

    digitalWrite(_cfg.pinRST, LOW);  delay(2);
    digitalWrite(_cfg.pinRST, HIGH); delay(10);

    mcp_reset();
    if (!mcp_setMode(MODE_CONFIG)) { Serial.println("[MCP] Cannot enter CONFIG"); return false; }
    if (!mcp_setBitTiming_125k())   { Serial.println("[MCP] Bit timing set failed"); return false; }
    mcp_acceptAll();
    mcp_bitmod(REG_EFLG, 0xFF, 0x00);
    if (!mcp_setMode(MODE_NORMAL))  { Serial.println("[MCP] Cannot enter NORMAL"); return false; }

    Serial.printf("[MCP] Ready @125 kbps (OSC=%u MHz, SAM=1)\n", _cfg.mcpOscMHz);
    dumpCNF();
    return true;
  }

  bool commProbe(uint32_t timeoutMs=800) {
    sendRead(_cfg.moduleAddr, Cmd::ModuleStatus);
    uint32_t t0 = millis(); uint8_t len=0; uint32_t id=0; uint8_t d[8];
    while (millis()-t0 < timeoutMs) if (mcp_receive_ext(id,d,len)) return true;
    return false;
  }

  bool powerOn(bool on=true) {
    if (!sendSet(_cfg.moduleAddr, Cmd::PowerOnOff, on ? 0u : 1u)) return false;
    uint32_t dummy=0; return waitResp(_cfg.moduleAddr, MsgType::SetDataResp, Cmd::PowerOnOff, dummy, 1200);
  }
  bool setVref_mV(uint32_t mV) {
    if (!sendSet(_cfg.moduleAddr, Cmd::VoutRef_mV, mV)) return false;
    uint32_t echo=0; return waitResp(_cfg.moduleAddr, MsgType::SetDataResp, Cmd::VoutRef_mV, echo, 800);
  }
  bool setILimit_mA(uint32_t mA) {
    if (!sendSet(_cfg.moduleAddr, Cmd::IoutLimit_mA, mA)) return false;
    uint32_t echo=0; return waitResp(_cfg.moduleAddr, MsgType::SetDataResp, Cmd::IoutLimit_mA, echo, 800);
  }
  bool readVout_mV(uint32_t &mV) {
    if (!sendRead(_cfg.moduleAddr, Cmd::Vout_mV)) return false;
    return waitResp(_cfg.moduleAddr, MsgType::ReadDataResp, Cmd::Vout_mV, mV, 800);
  }
  bool readIout_mA(uint32_t &mA) {
    if (!sendRead(_cfg.moduleAddr, Cmd::Iout_mA)) return false;
    return waitResp(_cfg.moduleAddr, MsgType::ReadDataResp, Cmd::Iout_mA, mA, 800);
  }

  bool setHiLoMode(HiLoMode mode) {
    uint8_t d[8] = { (uint8_t)((_cfg.groupNibble<<4)|MsgType::SetData), 0x5F, 0,0, 0,0,0,0 };
    put_be32(&d[4], (uint32_t)mode);
    if (!mcp_send_ext(buildId(_cfg.monitorAddr, _cfg.moduleAddr), d, 8)) return false;
    uint32_t echo=0; return waitResp(_cfg.moduleAddr, MsgType::SetDataResp, (Cmd)0x5F, echo, 1200);
  }
  bool readHiLoModeCfg(HiLoMode &mode) {
    if (!sendRead(_cfg.moduleAddr, (Cmd)0x60)) return false;
    uint32_t v=0; if (!waitResp(_cfg.moduleAddr, MsgType::ReadDataResp, (Cmd)0x60, v, 800)) return false;
    mode = (HiLoMode)(v & 0xFF); return true;
  }
  bool readHiLoModeActual(HiLoMode &mode) {
    if (!sendRead(_cfg.moduleAddr, (Cmd)0x65)) return false;
    uint32_t v=0; if (!waitResp(_cfg.moduleAddr, MsgType::ReadDataResp, (Cmd)0x65, v, 1200)) return false;
    mode = (HiLoMode)(v & 0xFF); return true;
  }

  bool ensureModeForTargetV(uint32_t target_mV, HiLoMode &actual_out) {
    HiLoMode actual = HiLoMode::LOW;
    if (!readHiLoModeActual(actual)) {
      if (!readHiLoModeCfg(actual)) actual = HiLoMode::LOW;
    }
    HiLoMode want = actual;
    if (actual == HiLoMode::LOW  && target_mV >= _cfg.hvEnter_mV) want = HiLoMode::HIGH;
    if (actual == HiLoMode::HIGH && target_mV <= _cfg.hvExit_mV)  want = HiLoMode::LOW;

    if (want == actual) { actual_out = actual; return true; }

    Serial.printf("[MODE] Switching %s -> %s\n",
      actual==HiLoMode::HIGH?"HIGH":"LOW", want==HiLoMode::HIGH?"HIGH":"LOW");

    (void)powerOn(false); delay(200);
    if (!setHiLoMode(want)) { actual_out = actual; return false; }
    (void)powerOn(true);

    uint32_t t0 = millis(); HiLoMode now = actual;
    while (millis()-t0 < 2000) {
      if (readHiLoModeActual(now) && now == want) { actual_out = now; return true; }
      delay(100);
    }
    actual_out = now; return (now == want);
  }

  bool setVoltageAuto(uint32_t target_V, uint32_t req_I_mA) {
    uint32_t target_mV = clampV_mV(target_V * 1000UL);

    HiLoMode active{};
    if (!ensureModeForTargetV(target_mV, active)) {
      Serial.println("[MODE] ensureModeForTargetV failed (continuing best-effort)");
    } else {
      Serial.printf("[MODE] Active=%s\n", active==HiLoMode::HIGH?"HIGH":
                                 active==HiLoMode::LOW ?"LOW":"AUTO");
    }

    uint32_t ilim_mA = pLimitedI_mA(target_mV, req_I_mA);
    if (!setILimit_mA(ilim_mA)) { Serial.println("[SET] Ilim no-ack"); dumpErrors("Ilimit"); }
    Serial.printf("[SET] Vref=%lu V, Ilim=%.3f A\n", (unsigned long)(target_mV/1000), ilim_mA/1000.0f);
    if (!setVref_mV(target_mV)) { Serial.println("[SET] Vref set no-ack"); dumpErrors("Vref"); }
    return true;
  }

  inline uint32_t clampV_mV(uint32_t v_mV) const {
    if (v_mV < _cfg.vMin_mV) return _cfg.vMin_mV;
    if (v_mV > _cfg.vMax_mV) return _cfg.vMax_mV;
    return v_mV;
  }
  inline uint32_t pLimitedI_mA(uint32_t v_mV, uint32_t req_mA) const {
    if (v_mV < 1000) return 0;
    uint32_t ipow_mA = (uint32_t)((_cfg.pMaxW * 1000ULL) / v_mV); // mA
    return (req_mA < ipow_mA) ? req_mA : ipow_mA;
  }

  void dumpErrors(const char* tag) const {
    uint8_t eflg=mcp_read(REG_EFLG), tec=mcp_read(REG_TEC), rec=mcp_read(REG_REC), intf=mcp_read(REG_CANINTF);
    Serial.printf("[DIAG] %s EFLG=0x%02X TEC=%u REC=%u CANINTF=0x%02X\n", tag, eflg, tec, rec, intf);
  }
  void dumpCNF() const {
    Serial.printf("[CNF] CNF1=0x%02X CNF2=0x%02X CNF3=0x%02X (OSC=%u MHz, %lu bps)\n",
                  mcp_read(REG_CNF1), mcp_read(REG_CNF2), mcp_read(REG_CNF3),
                  _cfg.mcpOscMHz, (unsigned long)_cfg.canBitrate);
  }

private:
  enum class MsgType : uint8_t { SetData=0x0, SetDataResp=0x1, ReadData=0x2, ReadDataResp=0x3 };
  enum class Cmd     : uint8_t { Vout_mV=0, Iout_mA=1, VoutRef_mV=2, IoutLimit_mA=3, PowerOnOff=4, ModuleStatus=8 };

  inline uint32_t buildId(uint8_t monitor, uint8_t module, uint8_t prodDay=0, uint16_t snLow=0) const {
    return ((uint32_t)0x1<<25) | ((uint32_t)(monitor&0x0F)<<21) | ((uint32_t)(module&0x7F)<<14)
         | ((uint32_t)(prodDay&0x1F)<<9) | (uint32_t)(snLow&0x1FF);
  }
  static inline void put_be32(uint8_t *p, uint32_t v) { p[0]=v>>24; p[1]=v>>16; p[2]=v>>8; p[3]=v; }

  bool sendSet(uint8_t module, Cmd cmd, uint32_t value) {
    uint8_t d[8] = { (uint8_t)((_cfg.groupNibble<<4)|(uint8_t)MsgType::SetData),
                     (uint8_t)cmd, 0, 0, 0,0,0,0 };
    put_be32(&d[4], value);
    return mcp_send_ext(buildId(_cfg.monitorAddr, module), d, 8);
  }
  bool sendRead(uint8_t module, Cmd cmd) {
    uint8_t d[8] = { (uint8_t)((_cfg.groupNibble<<4)|(uint8_t)MsgType::ReadData),
                     (uint8_t)cmd, 0, 0, 0,0,0,0 };
    return mcp_send_ext(buildId(_cfg.monitorAddr, module), d, 8);
  }
  bool waitResp(uint8_t module, MsgType expectMsg, Cmd expectCmd, uint32_t &value, uint32_t timeoutMs) {
    uint8_t data[8], len=0; uint32_t id=0; uint32_t t0=millis();
    while (millis()-t0 < timeoutMs) {
      if (mcp_receive_ext(id, data, len)) {
        uint8_t mod = (id >> 14) & 0x7F;
        uint8_t msg = data[0] & 0x0F;
        uint8_t cmd = data[1];
        if (mod==module && msg==(uint8_t)expectMsg && cmd==(uint8_t)expectCmd) {
          value = (len>=8) ? ((uint32_t)data[4]<<24 | (uint32_t)data[5]<<16 | (uint32_t)data[6]<<8 | data[7]) : 0;
          return true;
        }
      }
      delay(1);
    }
    return false;
  }

  // MCP2515 low-level
  static constexpr uint8_t INSTR_RESET  = 0xC0;
  static constexpr uint8_t INSTR_READ   = 0x03;
  static constexpr uint8_t INSTR_WRITE  = 0x02;
  static constexpr uint8_t INSTR_BITMOD = 0x05;
  static constexpr uint8_t INSTR_RTS    = 0x80;

  static constexpr uint8_t REG_CANSTAT  = 0x0E;
  static constexpr uint8_t REG_CANCTRL  = 0x0F;
  static constexpr uint8_t REG_CNF3     = 0x28;
  static constexpr uint8_t REG_CNF2     = 0x29;
  static constexpr uint8_t REG_CNF1     = 0x2A;
  static constexpr uint8_t REG_CANINTE  = 0x2B;
  static constexpr uint8_t REG_CANINTF  = 0x2C;
  static constexpr uint8_t REG_EFLG     = 0x2D;
  static constexpr uint8_t REG_TEC      = 0x1C;
  static constexpr uint8_t REG_REC      = 0x1D;
  static constexpr uint8_t REG_TXB0CTRL = 0x30;
  static constexpr uint8_t REG_TXB0SIDH = 0x31;
  static constexpr uint8_t REG_TXB0SIDL = 0x32;
  static constexpr uint8_t REG_TXB0EID8 = 0x33;
  static constexpr uint8_t REG_TXB0EID0 = 0x34;
  static constexpr uint8_t REG_TXB0DLC  = 0x35;
  static constexpr uint8_t REG_TXB0D0   = 0x36;

  static constexpr uint8_t REQOP_MASK   = 0xE0;
  static constexpr uint8_t MODE_NORMAL  = 0x00;
  static constexpr uint8_t MODE_CONFIG  = 0x80;

  inline void CS_LOW()  const { digitalWrite(_cfg.pinCS, LOW); }
  inline void CS_HIGH() const { digitalWrite(_cfg.pinCS, HIGH); }

  inline void mcp_reset() const { CS_LOW(); _spi.transfer(INSTR_RESET); CS_HIGH(); delay(5); }
  inline uint8_t mcp_read(uint8_t a) const { CS_LOW(); _spi.transfer(INSTR_READ); _spi.transfer(a); uint8_t v=_spi.transfer(0); CS_HIGH(); return v; }
  inline void mcp_write(uint8_t a, uint8_t v) const { CS_LOW(); _spi.transfer(INSTR_WRITE); _spi.transfer(a); _spi.transfer(v); CS_HIGH(); }
  inline void mcp_writes(uint8_t a, const uint8_t* d, size_t n) const { CS_LOW(); _spi.transfer(INSTR_WRITE); _spi.transfer(a); while(n--) _spi.transfer(*d++); CS_HIGH(); }
  inline void mcp_bitmod(uint8_t a, uint8_t m, uint8_t d) const { CS_LOW(); _spi.transfer(INSTR_BITMOD); _spi.transfer(a); _spi.transfer(m); _spi.transfer(d); CS_HIGH(); }

  bool mcp_setMode(uint8_t mode) const {
    mcp_bitmod(REG_CANCTRL, REQOP_MASK, mode);
    for (uint32_t t=millis(); millis()-t<50; ) if ((mcp_read(REG_CANSTAT)&REQOP_MASK)==mode) return true;
    return false;
  }

  bool mcp_setBitTiming_125k() const {
    uint8_t cnf1 = (_cfg.mcpOscMHz==16) ? 0x03 : 0x01; // BRP=(4 or 2)-1, SJW=1
    mcp_write(REG_CNF1, cnf1);
    mcp_write(REG_CNF2, 0xF1);
    mcp_write(REG_CNF3, 0x05);
    return true;
  }

  void mcp_acceptAll() const {
    mcp_write(0x60, 0x64); // RXB0: any + BUKT
    mcp_write(0x70, 0x60); // RXB1: any
    mcp_write(REG_CANINTE, 0x03);
  }

  static inline void id29_to_regs(uint32_t id, uint8_t& sidh, uint8_t& sidl, uint8_t& eid8, uint8_t& eid0) {
    uint16_t sid = (id >> 18) & 0x7FF;
    uint32_t eid = id & 0x3FFFF;
    sidh = (sid >> 3) & 0xFF;
    sidl = ((sid & 0x7) << 5) | (1<<3) | ((eid >> 16) & 0x3);
    eid8 = (eid >> 8) & 0xFF;
    eid0 = (eid & 0xFF);
  }
  static inline uint32_t regs_to_id29(uint8_t sidh, uint8_t sidl, uint8_t eid8, uint8_t eid0) {
    uint16_t sid = ((uint16_t)sidh<<3) | ((sidl>>5)&0x7);
    uint32_t eid = ((uint32_t)(sidl&0x3)<<16) | ((uint32_t)eid8<<8) | eid0;
    return ((uint32_t)sid<<18) | eid;
  }

  bool mcp_send_ext(uint32_t id, const uint8_t* data, uint8_t len) const {
    if (len > 8) len = 8;
    for (uint32_t t=millis(); millis()-t<50; ) if ((mcp_read(REG_TXB0CTRL)&0x08)==0) break;
    if (mcp_read(REG_TXB0CTRL)&0x08) return false;
    uint8_t sidh,sidl,eid8,eid0; id29_to_regs(id,sidh,sidl,eid8,eid0);
    uint8_t hdr[5] = { sidh,sidl,eid8,eid0,(uint8_t)(len&0x0F) };
    mcp_writes(REG_TXB0SIDH, hdr, 5);
    if (len) mcp_writes(REG_TXB0D0, data, len);
    CS_LOW(); _spi.transfer(INSTR_RTS | 0x01); CS_HIGH();
    return true;
  }
  bool mcp_receive_ext(uint32_t& id, uint8_t* data, uint8_t& len) const {
    uint8_t intf = mcp_read(REG_CANINTF);
    if (intf & 0x01) { // RXB0
      uint8_t b[13]; CS_LOW(); _spi.transfer(INSTR_READ); _spi.transfer(0x61);
      for (int i=0;i<13;i++) b[i]=_spi.transfer(0); CS_HIGH();
      id = regs_to_id29(b[0],b[1],b[2],b[3]); len=b[4]&0x0F; if(len>8) len=8; for(int i=5,j=0;j<len;i++,j++) data[j]=b[i];
      mcp_bitmod(REG_CANINTF, 0x01, 0x00); return true;
    }
    if (intf & 0x02) { // RXB1
      uint8_t b[13]; CS_LOW(); _spi.transfer(INSTR_READ); _spi.transfer(0x71);
      for (int i=0;i<13;i++) b[i]=_spi.transfer(0); CS_HIGH();
      id = regs_to_id29(b[0],b[1],b[2],b[3]); len=b[4]&0x0F; if(len>8) len=8; for(int i=5,j=0;j<len;i++,j++) data[j]=b[i];
      mcp_bitmod(REG_CANINTF, 0x02, 0x00); return true;
    }
    return false;
  }

private:
  Config   _cfg;
  SPIClass _spi;
};

} // namespace jp

