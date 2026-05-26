// CAN Bus Heartbeat Float - ESP32-C3 Super Mini + MCP2515
// Library: autowp/arduino-mcp2515

#define NA
// #define NB

#if defined(NA) && defined(NB)
#error "Define NA or NB, not both."
#endif
#if !defined(NA) && !defined(NB)
#error "You must define either NA or NB before flashing."
#endif

#include <SPI.h>
#include <mcp2515.h>

// -- Hardware config (ESP32-C3 SPI pins) ----------------------------------------
#define PIN_SCK 4
#define PIN_MISO 5
#define PIN_MOSI 6
#define PIN_CS 7
#define PIN_INT 3  // hardware interrupt pin
#define PIN_LED 8  // active LOW

// -- Node identity ---------------------------------------------------------------
#if defined(NA)
const uint32_t TX_ID = 0x40;
// const uint32_t RX_ID = 0x41;
const char *ND_NAME = "A";
#else
const uint32_t TX_ID = 0xA1;
// const uint32_t RX_ID = 0xA0;
const char *ND_NAME = "B";
#endif

// -- Timing ----------------------------------------------------------------------
const uint32_t TX_T = 500;
const uint32_t HT_T = 2000;

// -- LED -------------------------------------------------------------------------
const uint8_t LED_ON = LOW;
const uint8_t LED_OFF = HIGH;


const float THF = 115.5f;
const float TLF = 90.0f;
const uint8_t AV = 0xFF;

MCP2515 mcp2515(PIN_CS);

// -- State -----------------------------------------------------------------------
static bool swp = true;  // when true, sweep-based modes refresh on each TX cycle
static short md = 2;     // for payload generation: 0 = 0f, 1 = expo, 2 = tempsimu, 3 = realtemp, 4 manual
static uint32_t lastTx = 0;
static uint32_t lastHth = 0;

static bool txEn = true;
static bool rxEn = true;
static bool bErr = false;
static bool dbgEn = true;

static volatile bool canRxFlg = false;

static float data_f = 79.5f;
static float f[4] = { 0.0f, 0.0f, 0.0f, 0.0f };
static bool exd = true;  // var for dir calExpo

static can_frame txFrm;

static void setupCan();
static void hdlTx();
static can_frame buidlTxFrm();
static void buildStatus(float temp, uint8_t *status);
static float simT();
static float calcExpo();
static void hdlRx();
static void hdlFrm(const struct can_frame &frame);
static void chkCan();
static bool hdlBCmd(const String &cmd);
static bool hdlTCmd(const String &cmd);
static void hdlSer();

void IRAM_ATTR onCanInterrupt() {
  canRxFlg = true;
}

void setup() {
  Serial.begin(115200);

  uint32_t t = millis();
  while (!Serial && (millis() - t < 3000)) {
  }

  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LED_OFF);

  // Initialize SPI for ESP32-C3
  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CS);

  setupCan();

  // Attach the hardware interrupt
  pinMode(PIN_INT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_INT), onCanInterrupt, FALLING);

  if (dbgEn) {
    Serial.printf("[SYS] ID=0x%03X INT=GPIO%d\n",
                  ND_NAME, TX_ID, PIN_INT);
    Serial.println("[SYS] cmds: tx, ntx, rx, nrx, dn, up, pi, npi, dbg, ndbg");
    Serial.println("[SYS] temp cmds: te, nte, fx, st <val>");
  }
}

void loop() {
  hdlSer();
  hdlTx();
  hdlRx();
  chkCan();
}

static void setupCan() {
  if (dbgEn) Serial.println("[CAN] Initializing...");
  while (mcp2515.reset() != MCP2515::ERROR_OK) {
    if (dbgEn) Serial.println("[CAN] Reset failed, retrying...");
    delay(1000);
  }

  mcp2515.setBitrate(CAN_500KBPS, MCP_8MHZ);

  // Hardware filtering: accept only IDs 0x00 to 0xFF
  // Mask 0x700 = check only upper 3 bits, so accept IDs where bits 10-8 are 0
  mcp2515.setFilterMask(MCP2515::MASK0, false, 0x700);
  mcp2515.setFilterMask(MCP2515::MASK1, false, 0x700);
  mcp2515.setFilter(MCP2515::RXF0, false, 0x000);
  mcp2515.setFilter(MCP2515::RXF1, false, 0x000);
  mcp2515.setFilter(MCP2515::RXF2, false, 0x000);
  mcp2515.setFilter(MCP2515::RXF3, false, 0x000);

  // One-Shot Mode: prevents TX buffer lockups when the peer node is offline
  mcp2515.setNormalOneShotMode();
  if (dbgEn) Serial.println("[CAN] Ready.");
}

static void hdlTx() {
  if (!txEn)
    return;

  const uint32_t now = millis();
  if (now - lastTx < TX_T)
    return;
  lastTx = now;

  txFrm = buildTxFrm();
  MCP2515::ERROR status = mcp2515.sendMessage(&txFrm);
  if (status != MCP2515::ERROR_OK) {
    status = mcp2515.sendMessage(&txFrm);
  }

  if (status == MCP2515::ERROR_OK) {
    if (bErr) {
      if (dbgEn) Serial.printf("[BUS] TX OK - Bus recovered\n", ND_NAME);
      bErr = false;
    }
    uint64_t raw = 0;
    memcpy(&raw, txFrm.data, sizeof(raw));

    if (dbgEn) {
      Serial.printf("[TX]\t0x%03X|%d|0x%016llX|%.4f",
                    txFrm.can_id, txFrm.can_dlc, raw, data_f);
      if (txFrm.can_dlc > 4)
        Serial.printf("\tS:0x%02X-0x%02X", txFrm.data[5], txFrm.data[4]);
      Serial.println();
    } else {
      Serial.printf("!TX,%03X,%.4f,%02X,%02X\n", txFrm.can_id, data_f, txFrm.can_dlc > 4 ? txFrm.data[4] : 0, txFrm.can_dlc > 5 ? txFrm.data[5] : 0);
    }
  } else {
    if (dbgEn) Serial.printf("[TX] Dropped. Status=%d\n", ND_NAME, status);
  }
}

static can_frame buildTxFrm() {
  can_frame frame;
  memset(&frame, 0, sizeof(frame));
  frame.can_id = TX_ID;
  frame.can_dlc = 4;

  if (swp) {
    switch (md) {
      case 0: data_f = 0.0f; break;
      case 1: data_f = calcExpo(); break;
      case 2:
        data_f = simT(data_f);
        frame.can_dlc = 6;
        break;
      case 3: /* Real temp */ break;
    }
  }
  uint8_t rwB[4];
  memcpy(rwB, &data_f, 4);

  uint8_t swapB[4];
  
  swapB[0] = rwB[1];
  swapB[1] = rwB[0];
  swapB[2] = rwB[3];
  swapB[3] = rwB[2];

  memcpy(frame.data, swapB, 4);

  if (md == 2 || md == 3) {
    buildStatus(data_f, &frame.data[4]);
  }

  return frame;
}

static void buildStatus(float temp, uint8_t *status) {
  // Byte 4: Low Temp Alarm
  status[0] = (temp < TLF) ? AV : 0x00;

  // Byte 5: High Temp Alarm
  status[1] = (temp > THF) ? AV : 0x00;
}

static void swapBytes(uint8_t *output, const uint8_t *source) {
  if (source == NULL || output == NULL) return;
  output[0] = source[1];
  output[1] = source[0];
  output[2] = source[3];
  output[3] = source[2];
}

// Returns the current simulated temperature (degrees F).
// in 0.5 F steps, crossing both alarm thresholds on each pass.
static float simT(float val) {

  float temp = val += 0.32715f;
  if (temp > THF + 10.0f)
    temp = TLF - 10.0f;
  return temp;
}

static float calcExpo(void) {

  if (f[0] <= 1.0f) {
    exd = true;
  } else if (f[0] >= 1e6f) {
    exd = false;
  }

  float m;
  if (exd) {
    /* divergent IIR: sum of weights > 1, grows exponentially */
    m = 0.1f + f[0] + f[1] * 0.5f + f[2] * 0.25f + f[3] * 0.125f;
  } else {
    /* convergent IIR: sum of weights < 1, decays exponentially toward 0 */
    m = f[0] * 0.35f + f[1] * 0.175f + f[2] * 0.0875f + f[3] * 0.04375f;
  }

  f[3] = f[2];
  f[2] = f[1];
  f[1] = f[0];
  f[0] = m;

  return m;
}

static void hdlRx() {
  // Check both the ISR flag AND the physical pin state.
  // If the pin is stuck LOW, a FALLING edge was missed but we must still drain the buffer.
  // if (!canInterruptTriggered && digitalRead(PIN_INT) == HIGH)
  if (!canRxFlg) return;
  canRxFlg = false;

  while (digitalRead(PIN_INT) == LOW) {
    const uint8_t irq = mcp2515.getInterrupts();
    struct can_frame frame;
    bool readSomething = false;

    // Check Buffer 0
    if (irq & MCP2515::CANINTF_RX0IF) {
      if (mcp2515.readMessage(MCP2515::RXB0, &frame) == MCP2515::ERROR_OK) {
        if (rxEn)
          hdlFrm(frame);
        readSomething = true;
      }
    }

    // Check Buffer 1
    if (irq & MCP2515::CANINTF_RX1IF) {
      if (mcp2515.readMessage(MCP2515::RXB1, &frame) == MCP2515::ERROR_OK) {
        if (rxEn)
          hdlFrm(frame);
        readSomething = true;
      }
    }

    // Hardware safety break: if we read nothing but the pin is still low,
    // break out to prevent the ESP32 from getting stuck in an infinite loop.
    if (!readSomething) {
      mcp2515.clearInterrupts();  // Force clear all interrupts
      break;
    }
  }
}

static void hdlFrm(const struct can_frame &frame) {
  // Software guard: hardware filter covers this, but defends against misconfiguration.
  // Require at least 4 bytes for float extraction.
  // if (frame.can_id != RX_ID || frame.can_dlc < 4)
  // return;
  
  uint8_t dt[sizeof(frame.data)];
  uint64_t raw = 0;
  float conv;

  memcpy(dt, frame.data, sizeof(dt));
  
  swapBytes(dt, frame.data);
  
  memcpy(&conv, dt, sizeof(conv));
  memcpy(&raw, frame.data, sizeof(raw));

  bool tl = false;
  bool th = false;

  if (frame.can_dlc >= 6) {
    tl = (frame.data[4] & AV) != 0;
    th = (frame.data[5] & AV) != 0;
  }

  digitalWrite(PIN_LED, ( th || tl) ? LED_ON : LED_OFF);

  if (dbgEn) {
    Serial.printf("[RX]\t0x%03X|%d|0x%016llX|%.4f", frame.can_id, frame.can_dlc, raw, conv);
    Serial.printf("\tS:0x%02X-0x%02X", dt[5], dt[4]);
    Serial.printf(" %s%s\n", th ? "TH" : "", tl ? "TL" : "");
  } else {
    Serial.printf("!RX,%03X,%.4f,%02X,%02X\n", frame.can_id, conv, frame.can_dlc > 4 ? dt[4] : 0, frame.can_dlc > 5 ? dt[5] : 0);
  }
}

static void chkCan() {
  const uint32_t now = millis();
  if (now - lastHth < HT_T)
    return;
  lastHth = now;

  const uint8_t eflg = mcp2515.getErrorFlags();
  if (eflg == 0)
    return;

  if (eflg & (MCP2515::EFLG_RX0OVR | MCP2515::EFLG_RX1OVR)) {
    if (dbgEn) Serial.printf("[BUS] RX Overflow (0x%02X): %s%s\n", ND_NAME, eflg,
                  (eflg & MCP2515::EFLG_RX0OVR) ? "[RXB0] " : "",
                  (eflg & MCP2515::EFLG_RX1OVR) ? "[RXB1]" : "");
    mcp2515.clearRXnOVRFlags();
  }

  if (eflg & MCP2515::EFLG_TXBO) {
    if (!bErr) {
      bErr = true;
      if (dbgEn) Serial.printf("[BUS] CRITICAL: Bus-Off. TEC >= 256. Node disconnected.\n", ND_NAME);
    }
  } else if (eflg & (MCP2515::EFLG_TXEP | MCP2515::EFLG_RXEP)) {
    if (dbgEn) Serial.printf("[BUS] WARNING: Error-Passive. %s%s\n", ND_NAME,
                  (eflg & MCP2515::EFLG_TXEP) ? "[TX TEC >= 128] " : "",
                  (eflg & MCP2515::EFLG_RXEP) ? "[RX REC >= 128]" : "");
  } else if (eflg & MCP2515::EFLG_EWARN) {
    if (dbgEn) Serial.printf("[BUS] NOTICE: Error Warning. %s%s\n", ND_NAME,
                  (eflg & MCP2515::EFLG_TXWAR) ? "[TX TEC >= 96] " : "",
                  (eflg & MCP2515::EFLG_RXWAR) ? "[RX REC >= 96]" : "");
  }
}

static bool hdlBCmd(const String &cmd) {
  if (cmd == "tx") {
    txEn = true;
    if (dbgEn) Serial.println("[CMD] TX enabled");
  } else if (cmd == "ntx") {
    txEn = false;
    if (dbgEn) Serial.println("[CMD] TX disabled");
  } else if (cmd == "rx") {
    rxEn = true;
    if (dbgEn) Serial.println("[CMD] RX enabled");
  } else if (cmd == "nrx") {
    rxEn = false;
    if (dbgEn) Serial.println("[CMD] RX disabled");
  } else if (cmd == "dn") {
    txEn = rxEn = false;
    if (dbgEn) Serial.println("[CMD] dn - idle");
  } else if (cmd == "up") {
    txEn = rxEn = true;
    if (dbgEn) Serial.println("[CMD] up - TX and RX enabled");
  } else if (cmd == "sw") {
    swp = true;
    if (dbgEn) Serial.println("[CMD] Sweep enabled");
  } else if (cmd == "nsw") {
    swp = false;
    if (dbgEn) Serial.println("[CMD] Sweep disabled");
  } else if (cmd.startsWith("st ")) {
    data_f = cmd.substring(3).toFloat();
    swp = false;
    md = 4;
    if (dbgEn) Serial.printf("[CMD] float set to %.2f F\n", data_f);
  } else if (cmd.startsWith("m ")) {
    md = cmd.substring(2).toInt();
    // For modes 1 and 2, we want to keep swp=true so they update on each cycle. For other modes, default to no sweep.
    if (md == 1 || md == 2) swp = true; else swp = false; 
    if (dbgEn) Serial.printf("[CMD] mode change to %d\n", md);
  } else if (cmd == "dbg") {
    dbgEn = true;
    Serial.println("[CMD] Debug enabled");
  } else if (cmd == "ndbg") {
    dbgEn = false;
  } else
    return false;
  return true;
}

static bool hdlTCmd(const String &cmd) {
  if (cmd == "te") {
    swp = true;
    md = 2;
    if (dbgEn) Serial.println("[CMD] Temp mode ON (sweep 80->125 F)");
  } else if (cmd.startsWith("te ")) {
    data_f = cmd.substring(3).toFloat();
    swp = true;
    md = 2;
    if (dbgEn) Serial.printf("[CMD] Temp mode ON, sweep at %.2f F\n", data_f);
  } else if (cmd == "nte") {
    swp = false;
    md = 0;
    if (dbgEn) Serial.println("[CMD] Temp mode OFF");
  } else
    return false;
  return true;
}

static void hdlSer() {
  if (!Serial.available())
    return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toLowerCase();

  if (!hdlBCmd(cmd) && !hdlTCmd(cmd))
    if (dbgEn) Serial.printf("[CMD] unknown: %s\n", cmd.c_str());
}
