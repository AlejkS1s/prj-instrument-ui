// CAN Bus Heartbeat Float - ESP32-C3 Super Mini + MCP2515
// Library: autowp/arduino-mcp2515

#include <SPI.h>
#include <esp_adc/adc_cali.h>
#include <esp_adc/adc_cali_scheme.h>
#include <esp_adc/adc_oneshot.h>
#include <mcp2515.h>

#define NA
// #define NB

#if defined(NA) && defined(NB)
#error "Define NA or NB, not both."
#endif
#if !defined(NA) && !defined(NB)
#error "You must define either NA or NB before flashing."
#endif

// -- Node identity
// ---------------------------------------------------------------
#if defined(NA)
const uint32_t TX_ID = 0x40;
const char* ND_NAME = "A";
#else
const uint32_t TX_ID = 0xA1;
const char* ND_NAME = "B";
#endif

// -- Hardware config (ESP32-C3 SPI pins)
// ----------------------------------------
#define PIN_SCK 4
#define PIN_MISO 5
#define PIN_MOSI 6
#define PIN_CS 7
#define PIN_INT 3  // hardware interrupt pin
#define PIN_LED 8  // active LOW

// -- ADC Config -----------------------------------------
#define ADC_CHANNEL ADC_CHANNEL_0  // GPIO0
#define ADC_OVERSAMPLE 32
#define FILTER_SIZE 16
#define V_ADC_GAIN 2.01537  // 2.015372168284790
#define ADC_SLOPE 1.0009
// #define ADC_SLOPE       0.99801035
#define ADC_OFFSET -5.95856
#define V_AMP_GAIN 11.25111

// -- Sensor State Thresholds
#define MIN_FLT_V 0.15
#define MAX_FLT_V 3.00

// -- Sensor State Codification
// ------------------------------------------------------------------
enum SensorState : short {
  STATE_NORMAL = 0,
  STATE_DISCONNECTED = 1,   // Voltage drops near 0V
  STATE_SHORT_CIRCUIT = 2,  // Voltage pegs to upper rail
  STATE_STUCK = 3,          // Zero variance over time (frozen front-end)
  STATE_NOISY = 4           // High variance (EMC interference/unstable)
};

// -- Timing
// ----------------------------------------------------------------------
const uint32_t TX_T = 500;
const uint32_t HT_T = 2000;
const uint32_t SENS_T = 20;
const uint32_t SENS_EV_T = 200;

// -- Thresholds & Status
// ---------------------------------------------------------
const uint8_t LED_ON = LOW;
const uint8_t LED_OFF = HIGH;
const float THF = 115.5f;
const float TLF = 90.0f;
const uint8_t AV = 0xFF;

// -- LUT for PT100 (Voltage to Fahrenheit)
// --------------------------------------- const double lut_voltage[] =
// {1.12, 1.22, 1.38, 1.53, 1.69, 1.87, 2.01, 2.18, 2.35, 2.52, 2.70}; const
// double lut_temp[] = {80.8, 85.1, 95.2, 105.1, 115.2, 125.2, 135.5, 145.6,
// 155.5, 165.4, 175.6};
const float lut_voltage[] = {0.89f, 0.99f, 1.09f, 1.25f, 1.38f, 1.56f,
                             1.74f, 1.88f, 2.05f, 2.22f, 2.39f, 2.57f};
const float lut_temp[] = {77.4f,  83.86f, 88.5f,  98.4f,  108.3f, 118.4f,
                          127.4f, 137.6f, 147.9f, 157.9f, 167.9f, 177.9f};
const int lut_size = 12;
// -- Pre-calculated Single-Precision Slopes
static float lut_slopes[lut_size - 1];

// -- Global Handles & State
// ------------------------------------------------------
MCP2515 mcp2515(PIN_CS);
static adc_oneshot_unit_handle_t adcHandle;
static adc_cali_handle_t adcCaliHandle;
// when true, sweep-based modes refresh on each TX cycle
static bool swp = false;
// for payload generation: 0 = 0f, 1 = expo, 2 = tempsimu,
// 3 = realtemp, 4 manual
static short md = 3;

static uint32_t lastTx = 0;
static uint32_t lastHth = 0;
static uint32_t lastSen = 0;
static uint32_t lastSenEval = 0;
static bool ownAlm = false;  // if true, also activate the onboard LED for local alarm

static bool txEn = true;
static bool rxEn = true;
static bool bErr = false;
static bool dbgEn = true;

static volatile bool canRxFlg = false;

static float data_f = 79.5f;
static float temp_f = 0.0f;
static short senState = STATE_NORMAL;
static float f[4] = {0.0f, 0.0f, 0.0f, 0.0f};
static bool exd = true;  // var for dir calExpo

// ADC Filter state
static float filterBuf[FILTER_SIZE] = {0};
static uint8_t filterIdx = 0;
static float filterSum = 0.0f;

static can_frame txFrm;

// -- Function Prototypes
// ---------------------------------------------------------
static void setupCan();
static void initADC();
static void initLutSlopes();
static void hdlSensor();
static float readOversampled();
static float upMovAv(float c_mV);
static float vToTempLUT(float voltage_v);
static void chkSenState(float vOut);
static short calcSenState(float vOut);
static void hdlTx();
static can_frame buildTxFrm();
static void buildStatus(float temp, short state, uint8_t* status);
static void swapBytes(uint8_t* output, const uint8_t* source);
static float simT(float val);
static float calcExpo();
static void hdlRx();
static void hdlFrm(const struct can_frame& frame);
static void chkCan();
static void hdlSer();

void IRAM_ATTR onCanInterrupt() { canRxFlg = true; }

void setup() {
  Serial.begin(115200);

  uint32_t t = millis();
  while (!Serial && (millis() - t < 3000)) {
  }

  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LED_OFF);

  initADC();
  initLutSlopes();

  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CS);
  setupCan();

  // Attach the hardware interrupt
  pinMode(PIN_INT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_INT), onCanInterrupt, FALLING);

  if (dbgEn) {
    Serial.printf("[SYS] ID=0x%03X INT=GPIO%d\n", ND_NAME, TX_ID, PIN_INT);
    Serial.println("[SYS] cmds: tx, ntx, rx, nrx, dn, up, pi, npi, dbg, ndbg");
    Serial.println("[SYS] temp cmds: te, nte, fx, st <val>");
  }
}

void loop() {
  hdlSer();
  hdlSensor();
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

static void initADC() {
  adc_oneshot_unit_init_cfg_t unitCfg = {
      .unit_id = ADC_UNIT_1,
      .ulp_mode = ADC_ULP_MODE_DISABLE,
  };
  adc_oneshot_new_unit(&unitCfg, &adcHandle);

  adc_oneshot_chan_cfg_t chanCfg = {
      .atten = ADC_ATTEN_DB_6,
      .bitwidth = ADC_BITWIDTH_12,
  };
  adc_oneshot_config_channel(adcHandle, ADC_CHANNEL, &chanCfg);

  adc_cali_curve_fitting_config_t caliCfg = {
      .unit_id = ADC_UNIT_1,
      .chan = ADC_CHANNEL,
      .atten = ADC_ATTEN_DB_6,
      .bitwidth = ADC_BITWIDTH_12,
  };
  adc_cali_create_scheme_curve_fitting(&caliCfg, &adcCaliHandle);
}

void initLutSlopes() {
  for (int i = 0; i < lut_size - 1; i++) {
    lut_slopes[i] =
        (lut_temp[i + 1] - lut_temp[i]) / (lut_voltage[i + 1] - lut_voltage[i]);
  }
}

static void hdlSensor() {
  const uint32_t now = millis();
  if (now - lastSen < SENS_T) return;
  lastSen = now;

  float raw_mV = readOversampled();
  float f_mV = upMovAv(raw_mV);
  float vOut = (f_mV * V_ADC_GAIN) / 1000.0;

  chkSenState(vOut);

  if (senState == STATE_NORMAL || senState == STATE_NOISY) {
    temp_f = vToTempLUT(vOut);
  } else {
    temp_f = -999.0f;  // Structural fallback value for bad data
  }

  if(ownAlm) digitalWrite(PIN_LED,
               (temp_f > THF || temp_f < TLF || senState != STATE_NORMAL)
                   ? LED_ON
                   : LED_OFF);
}

static float readOversampled() {
  int32_t acc = 0;
  int raw;
  for (uint8_t i = 0; i < ADC_OVERSAMPLE; i++) {
    adc_oneshot_read(adcHandle, ADC_CHANNEL, &raw);
    acc += raw;
  }
  int raw_avg = acc / ADC_OVERSAMPLE;
  int mV;
  adc_cali_raw_to_voltage(adcCaliHandle, raw_avg, &mV);

  float corrected = (mV * ADC_SLOPE) + ADC_OFFSET;
  return (corrected < 0.0f) ? 0.0f : corrected;
}

static float upMovAv(float c_mV) {
  filterSum -= filterBuf[filterIdx];
  filterBuf[filterIdx] = c_mV;
  filterSum += c_mV;
  filterIdx = (filterIdx + 1) % FILTER_SIZE;
  return filterSum / FILTER_SIZE;
}

static float vToTempLUT(float voltage_v) {
  //  Boundary Guard Assertions (Hardware-accelerated comparisons)
  if (voltage_v <= lut_voltage[0]) {
    return lut_temp[0] + lut_slopes[0] * (voltage_v - lut_voltage[0]);
  }
  if (voltage_v >= lut_voltage[lut_size - 1]) {
    constexpr int last = lut_size - 1;
    constexpr int prev_idx = lut_size - 2;
    return lut_temp[last] +
           lut_slopes[prev_idx] * (voltage_v - lut_voltage[last]);
  }

  // Binary Search for Target Segment
  int low = 0;
  int high = lut_size - 2;
  int i = 0;

  while (low <= high) {
    int mid = low + ((high - low) >> 1);  // Bitwise right-shift division

    if (voltage_v < lut_voltage[mid]) {
      high = mid - 1;
    } else if (voltage_v > lut_voltage[mid + 1]) {
      low = mid + 1;
    } else {
      i = mid;  // Exact slice localized
      break;
    }
  }

  // Fully Hardware-Accelerated FPU Calculation (Zero Emulation Overhead)
  return lut_temp[i] + lut_slopes[i] * (voltage_v - lut_voltage[i]);
}

static void chkSenState(float vOut) {
  const uint32_t now = millis();
  // Executes heavy looping and variance statistics independently every 200ms
  if (now - lastSenEval >= SENS_EV_T) {
    lastSenEval = now;
    senState = calcSenState(vOut);
  }
}

static short calcSenState(float vOut) {
  // Structural Voltage Bounds Check
  if (vOut < MIN_FLT_V) return STATE_DISCONNECTED;
  if (vOut > MAX_FLT_V) return STATE_SHORT_CIRCUIT;

  // Statistical Analysis of the Moving Target Array
  bool identical = true;
  float baseline = filterBuf[0];
  float mean = filterSum / FILTER_SIZE;
  float sumSqDiff = 0.0f;

  for (uint8_t i = 0; i < FILTER_SIZE; i++) {
    if (abs(filterBuf[i] - baseline) > 0.0001) {
      identical = false;
    }
    float diff = filterBuf[i] - mean;
    sumSqDiff += diff * diff;
  }

  if (identical) return STATE_STUCK;

  // Variance calculation for high-frequency runtime anomaly checks
  float variance = sumSqDiff / FILTER_SIZE;
  if (variance > 1500.0f) return STATE_NOISY;

  return STATE_NORMAL;
}

static void hdlTx() {
  if (!txEn) return;

  const uint32_t now = millis();
  if (now - lastTx < TX_T) return;
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
      Serial.printf("[TX]\t0x%03X|%d|0x%016llX|%.4f", txFrm.can_id,
                    txFrm.can_dlc, raw, data_f);
      if (txFrm.can_dlc > 4)
        Serial.printf("\tS:0x%02X-0x%02X", txFrm.data[5], txFrm.data[4]);
      Serial.println();
    } else {
      Serial.printf("!TX,%03X,%.4f,%02X,%02X\n", txFrm.can_id, data_f,
                    txFrm.can_dlc > 4 ? txFrm.data[4] : 0,
                    txFrm.can_dlc > 5 ? txFrm.data[5] : 0);
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

  switch (md) {
    case 0:
      data_f = 0.0f;
      break;
    case 1:
      data_f = swp ? calcExpo() : data_f;
      break;
    case 2:
      data_f = swp ? simT(data_f) : data_f;
      frame.can_dlc = 6;
      break;
    case 3:
      // ignores swp - always send real temp on each cycle
      data_f = temp_f;
      frame.can_dlc = 6;
      break;
  }

  uint8_t rwB[4];
  memcpy(rwB, &data_f, 4);

  uint8_t swapB[4];

  swapB[0] = rwB[1];
  swapB[1] = rwB[0];
  swapB[2] = rwB[3];
  swapB[3] = rwB[2];

  memcpy(frame.data, swapB, 4);

  if (md == 3) {
    buildStatus(data_f, senState, &frame.data[4]);
  } else if (md == 2) {
    // For the temperature simulation mode,we want to encode status based on the
    // simulated temp, not the real temp, to allow testing of the full system
    // response to alarms.
    buildStatus(data_f, STATE_NORMAL, &frame.data[4]);
  }
  return frame;
}

static void buildStatus(float data, short state, uint8_t* status) {
  status[0] = 0x00;  // Byte 4 Initialization
  status[1] = 0x00;  // Byte 5 Initialization

  // Critical Prioritization: Thermal Window Limits Assertions
  if (state == STATE_NORMAL || state == STATE_NOISY) {
    if (data < TLF) status[0] = AV;  // Byte 4 Low Temp Alarm = 0xFF
    if (data > THF) status[1] = AV;  // Byte 5 High Temp Alarm = 0xFF
    return;
  }

  // Custom Encodings for Hardware Diagnostics (Bypasses range alarms during
  // faults)
  switch (state) {
    case STATE_DISCONNECTED:
      status[0] = 0x11;
      status[1] = 0x11;
      break;
    case STATE_SHORT_CIRCUIT:
      status[0] = 0x22;
      status[1] = 0x22;
      break;
    case STATE_STUCK:
      status[0] = 0x33;
      status[1] = 0x33;
      break;
  }
}

static void swapBytes(uint8_t* output, const uint8_t* source) {
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
  if (temp > THF + 10.0f) temp = TLF - 10.0f;
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
  // If the pin is stuck LOW, a FALLING edge was missed but we must still drain
  // the buffer. if (!canInterruptTriggered && digitalRead(PIN_INT) == HIGH)
  if (!canRxFlg) return;
  canRxFlg = false;

  while (digitalRead(PIN_INT) == LOW) {
    const uint8_t irq = mcp2515.getInterrupts();
    struct can_frame frame;
    bool readSomething = false;

    // Check Buffer 0
    if (irq & MCP2515::CANINTF_RX0IF) {
      if (mcp2515.readMessage(MCP2515::RXB0, &frame) == MCP2515::ERROR_OK) {
        if (rxEn) hdlFrm(frame);
        readSomething = true;
      }
    }

    // Check Buffer 1
    if (irq & MCP2515::CANINTF_RX1IF) {
      if (mcp2515.readMessage(MCP2515::RXB1, &frame) == MCP2515::ERROR_OK) {
        if (rxEn) hdlFrm(frame);
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

static void hdlFrm(const struct can_frame& frame) {
  // Software guard: hardware filter covers this, but defends against
  // misconfiguration. Require at least 4 bytes for float extraction. if
  // (frame.can_id != RX_ID || frame.can_dlc < 4) return;

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

  digitalWrite(PIN_LED, (th || tl) ? LED_ON : LED_OFF);

  if (dbgEn) {
    Serial.printf("[RX]\t0x%03X|%d|0x%016llX|%.4f", frame.can_id, frame.can_dlc,
                  raw, conv);
    Serial.printf("\tS:0x%02X-0x%02X", dt[5], dt[4]);
    Serial.printf(" %s%s\n", th ? "TH" : "", tl ? "TL" : "");
  } else {
    Serial.printf("!RX,%03X,%.4f,%02X,%02X\n", frame.can_id, conv,
                  frame.can_dlc > 4 ? dt[4] : 0, frame.can_dlc > 5 ? dt[5] : 0);
  }
}

static void chkCan() {
  const uint32_t now = millis();
  if (now - lastHth < HT_T) return;
  lastHth = now;

  const uint8_t eflg = mcp2515.getErrorFlags();
  if (eflg == 0) return;

  if (eflg & (MCP2515::EFLG_RX0OVR | MCP2515::EFLG_RX1OVR)) {
    if (dbgEn)
      Serial.printf("[BUS] RX Overflow (0x%02X): %s%s\n", ND_NAME, eflg,
                    (eflg & MCP2515::EFLG_RX0OVR) ? "[RXB0] " : "",
                    (eflg & MCP2515::EFLG_RX1OVR) ? "[RXB1]" : "");
    // Output error in non-debug format for UI processing
    Serial.printf("!ERR,OVF,0x%02X,RX%s%s\n", eflg,
                  (eflg & MCP2515::EFLG_RX0OVR) ? "B0" : "",
                  (eflg & MCP2515::EFLG_RX1OVR) ? "B1" : "");
    mcp2515.clearRXnOVRFlags();
  }

  if (eflg & MCP2515::EFLG_TXBO) {
    if (!bErr) {
      bErr = true;
      if (dbgEn)
        Serial.printf(
            "[BUS] CRITICAL: Bus-Off. TEC >= 256. Node disconnected.\n",
            ND_NAME);
      // Output error in non-debug format for UI processing
      Serial.printf("!ERR,BOFF,0x%02X,Bus-Off\n", eflg);
    }
  } else if (eflg & (MCP2515::EFLG_TXEP | MCP2515::EFLG_RXEP)) {
    if (dbgEn)
      Serial.printf("[BUS] WARNING: Error-Passive. %s%s\n", ND_NAME,
                    (eflg & MCP2515::EFLG_TXEP) ? "[TX TEC >= 128] " : "",
                    (eflg & MCP2515::EFLG_RXEP) ? "[RX REC >= 128]" : "");
    // Output error in non-debug format for UI processing
    Serial.printf("!ERR,EPR,0x%02X,%s%s\n", eflg,
                  (eflg & MCP2515::EFLG_TXEP) ? "TX" : "",
                  (eflg & MCP2515::EFLG_RXEP) ? "RX" : "");
  } else if (eflg & MCP2515::EFLG_EWARN) {
    if (dbgEn)
      Serial.printf("[BUS] NOTICE: Error Warning. %s%s\n", ND_NAME,
                    (eflg & MCP2515::EFLG_TXWAR) ? "[TX TEC >= 96] " : "",
                    (eflg & MCP2515::EFLG_RXWAR) ? "[RX REC >= 96]" : "");
    // Output error in non-debug format for UI processing
    Serial.printf("!ERR,EWR,0x%02X,%s%s\n", eflg,
                  (eflg & MCP2515::EFLG_TXWAR) ? "TX" : "",
                  (eflg & MCP2515::EFLG_RXWAR) ? "RX" : "");
  }
}

static bool hdlBCmd(const String& cmd) {
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
    // For modes 1 and 2, we want to keep swp=true so they update on each cycle.
    // For other modes, default to no sweep.
    if (md == 1 || md == 2)
      swp = true;
    else
      swp = false;
    if (dbgEn) Serial.printf("[CMD] mode change to %d\n", md);
  } else if (cmd == "alm") {
    ownAlm = !ownAlm;
    if (!ownAlm) digitalWrite(PIN_LED, LED_OFF);  // turn off LED if disabling alarm
    if (dbgEn) Serial.printf("[CMD] Onboard alarm %s\n", ownAlm ? "enabled" : "disabled");
  } else if (cmd == "dbg") {
    dbgEn = true;
    Serial.println("[CMD] Debug enabled");
  } else if (cmd == "ndbg") {
    dbgEn = false;
  } else
    return false;
  return true;
}

static bool hdlTCmd(const String& cmd) {
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
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toLowerCase();

  if (!hdlBCmd(cmd) && !hdlTCmd(cmd))
    if (dbgEn) Serial.printf("[CMD] unknown: %s\n", cmd.c_str());
}