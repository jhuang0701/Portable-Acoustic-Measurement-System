// acoustic_hil.ino: command-driven firmware for the Python hardware-in-the-loop sweep (hil_sweep.py).
// Hookup (unchanged from the manual sweeps): GPIO25 -> 100k -> junction; junction -> TP2 (MIC_IN);
// junction -> 100 ohm -> TP3 (VREF). Capsule unplugged. ADC on GPIO34 at 80 kSPS. Gain: A0 = GPIO18, A1 = GPIO19.
// Arduino Tools: Flash Mode = DIO, Flash Frequency = 40 MHz. Close the Serial Monitor before running the script.
//
// Protocol: one ASCII command per line (115200 baud at boot; the script switches to a faster rate with BAUD).
//   PING                  -> PONG
//   VER                   -> VER <firmware version> fs=80000 nmax=16384
//   BAUD <rate>           -> OK BAUD <rate>   (reply is sent at the old rate, then the rate changes)
//   GAIN <0..3>           -> OK GAIN <g>      (TMUX1104 select: nominal gains 1, 10.1, 101, 1001)
//   SWEEP <freq_hz> <amp> -> OK SWEEP ...     (LEDC square wave on GPIO25, exact frequency; amp = duty code 1..512 out of 1024,
//                                              512 = full 50 % square; fundamental scales as sin(pi * amp / 1024))
//   DAC <freq_hz> <att>   -> OK DAC ...       (DAC cosine on GPIO25, att 0 = default, 1 = 6 dB, 2 = 12 dB; frequency is approximate)
//   STOP                  -> OK STOP          (GPIO25 driven low)
//   CAPTURE <n>           -> DATA <n> <min> <max> <sum of samples mod 65536>   (samples are kept on the board, nothing else is sent)
//   GET <k>               -> CHUNK <k> <len> <byte sum mod 65536>\n, then <len> bytes: bytes k*1024 .. of the capture
//                            (uint16 little endian, samples exactly as delivered, NO swap-back applied here). The host can
//                            repeat GET for any chunk that arrives short or with a wrong sum.
//   n must be even, 1024..16384. Any error: ERR <reason>.
#include <math.h>
#include "esp_adc/adc_continuous.h"
#include "driver/dac_cosine.h"

#define FW_VERSION "acoustic_hil_1.1"
#define CHUNK_BYTES 1024
#define NMAX 16384

const int PIN_A0 = 18;
const int PIN_A1 = 19;
const int PIN_SRC = 25;
const int FRAME_BYTES = 1024;

adc_continuous_handle_t adc = NULL;
dac_cosine_handle_t dac = NULL;
uint8_t buf[FRAME_BYTES];
uint16_t *raw = NULL;
int rawMin, rawMax;
int lastN = 0;
bool srcAttached = false;

void adcStart() {
  adc_continuous_handle_cfg_t hc = {};
  hc.max_store_buf_size = 8192;
  hc.conv_frame_size = FRAME_BYTES;
  ESP_ERROR_CHECK(adc_continuous_new_handle(&hc, &adc));
  adc_digi_pattern_config_t pat = {};
  pat.atten = ADC_ATTEN_DB_12;
  pat.channel = ADC_CHANNEL_6;      // GPIO34 (filter output)
  pat.unit = ADC_UNIT_1;
  pat.bit_width = ADC_BITWIDTH_12;
  adc_continuous_config_t cfg = {};
  cfg.pattern_num = 1;
  cfg.adc_pattern = &pat;
  cfg.sample_freq_hz = 80000;
  cfg.conv_mode = ADC_CONV_SINGLE_UNIT_1;
  cfg.format = ADC_DIGI_OUTPUT_FORMAT_TYPE1;
  ESP_ERROR_CHECK(adc_continuous_config(adc, &cfg));
  ESP_ERROR_CHECK(adc_continuous_start(adc));
}

void setGain(int g) {
  digitalWrite(PIN_A0, g & 1);
  digitalWrite(PIN_A1, (g >> 1) & 1);
}

void drainMs(uint32_t ms) {
  uint32_t t0 = millis(), len = 0;
  while (millis() - t0 < ms) adc_continuous_read(adc, buf, FRAME_BYTES, &len, 20);
}

// capture n samples into raw[]; samples stay in the order the ADC driver delivers them
void captureN(int n) {
  int got = 0;
  uint32_t len = 0;
  rawMin = 5000; rawMax = 0;
  while (got < n) {
    if (adc_continuous_read(adc, buf, FRAME_BYTES, &len, 100) != ESP_OK) continue;
    for (uint32_t i = 0; i + SOC_ADC_DIGI_RESULT_BYTES <= len && got < n; i += SOC_ADC_DIGI_RESULT_BYTES) {
      adc_digi_output_data_t *p = (adc_digi_output_data_t *)&buf[i];
      int v = p->type1.data;
      if (v < rawMin) rawMin = v;
      if (v > rawMax) rawMax = v;
      raw[got++] = (uint16_t)v;
    }
  }
}

// ---- sources on GPIO25 (only one at a time)
void dacStop() {
  if (dac) { dac_cosine_stop(dac); dac_cosine_del_channel(dac); dac = NULL; }
}
void sqStop() {
  if (srcAttached) { ledcWrite(PIN_SRC, 0); ledcDetach(PIN_SRC); srcAttached = false; }
}
void sqStart(uint32_t f, uint32_t amp) {
  dacStop();
  uint8_t res = ((uint64_t)f * 1024ULL <= 78000000ULL) ? 10 : 8;
  if (!srcAttached) { ledcAttach(PIN_SRC, f, res); srcAttached = true; }
  else ledcChangeFrequency(PIN_SRC, f, res);
  ledcWrite(PIN_SRC, res == 10 ? amp : amp / 4);   // amp 512 of 1024 = 50 % duty = full square wave
}
void dacStart(uint32_t f, dac_cosine_atten_t att) {
  sqStop();
  dacStop();
  dac_cosine_config_t c = {};
  c.chan_id = DAC_CHAN_0;                    // GPIO25
  c.freq_hz = f;
  c.clk_src = DAC_COSINE_CLK_SRC_DEFAULT;
  c.atten = att;
  c.phase = DAC_COSINE_PHASE_0;
  c.offset = 0;
  c.flags.force_set_freq = true;
  ESP_ERROR_CHECK(dac_cosine_new_channel(&c, &dac));
  ESP_ERROR_CHECK(dac_cosine_start(dac));
}
void srcOff() {
  sqStop();
  dacStop();
  pinMode(PIN_SRC, OUTPUT);
  digitalWrite(PIN_SRC, LOW);
}

// ---- command handling
// Serial.write may accept fewer bytes than asked when the transmit buffer is full: keep writing until everything is queued
static void writeAll(const uint8_t *p, size_t len) {
  size_t sent = 0;
  while (sent < len) {
    size_t w = Serial.write(p + sent, len - sent);
    sent += w;
    if (w == 0) delay(1);
  }
}

void doCapture(int n) {
  captureN(n);
  lastN = n;
  uint32_t sum = 0;
  for (int i = 0; i < n; i++) sum += raw[i];
  Serial.printf("DATA %d %d %d %u\n", n, rawMin, rawMax, (unsigned)(sum & 0xFFFF));
}

void doGet(int k) {
  int total = lastN * 2;
  int off = k * CHUNK_BYTES;
  if (lastN == 0 || k < 0 || off >= total) { Serial.println("ERR no such chunk"); return; }
  int len = (total - off < CHUNK_BYTES) ? (total - off) : CHUNK_BYTES;
  const uint8_t *p = ((const uint8_t *)raw) + off;       // ESP32 is little endian
  uint32_t sum = 0;
  for (int i = 0; i < len; i++) sum += p[i];
  Serial.printf("CHUNK %d %d %u\n", k, len, (unsigned)(sum & 0xFFFF));
  writeAll(p, len);
}

void handleLine(char *line) {
  char *cmd = strtok(line, " \t\r");
  if (!cmd) return;
  char *a1 = strtok(NULL, " \t\r");
  char *a2 = strtok(NULL, " \t\r");
  if (!strcmp(cmd, "PING")) { Serial.println("PONG"); }
  else if (!strcmp(cmd, "VER")) { Serial.printf("VER %s fs=80000 nmax=%d\n", FW_VERSION, NMAX); }
  else if (!strcmp(cmd, "BAUD") && a1) {
    long rate = atol(a1);
    if (rate < 9600 || rate > 2000000) { Serial.println("ERR bad baud"); return; }
    Serial.printf("OK BAUD %ld\n", rate);
    Serial.flush();
    delay(50);
    Serial.updateBaudRate(rate);
  }
  else if (!strcmp(cmd, "GAIN") && a1) {
    int g = atoi(a1);
    if (g < 0 || g > 3) { Serial.println("ERR gain 0..3"); return; }
    setGain(g);
    Serial.printf("OK GAIN %d\n", g);
  }
  else if (!strcmp(cmd, "SWEEP") && a1 && a2) {
    long f = atol(a1), amp = atol(a2);
    if (f < 50 || f > 200000 || amp < 1 || amp > 512) { Serial.println("ERR freq 50..200000, amp 1..512"); return; }
    sqStart((uint32_t)f, (uint32_t)amp);
    Serial.printf("OK SWEEP %ld %ld\n", f, amp);
  }
  else if (!strcmp(cmd, "DAC") && a1 && a2) {
    long f = atol(a1), att = atol(a2);
    if (f < 130 || f > 55000 || att < 0 || att > 2) { Serial.println("ERR freq 130..55000, att 0..2"); return; }
    dacStart((uint32_t)f, att == 0 ? DAC_COSINE_ATTEN_DEFAULT : (att == 1 ? DAC_COSINE_ATTEN_DB_6 : DAC_COSINE_ATTEN_DB_12));
    Serial.printf("OK DAC %ld %ld\n", f, att);
  }
  else if (!strcmp(cmd, "STOP")) { srcOff(); Serial.println("OK STOP"); }
  else if (!strcmp(cmd, "CAPTURE") && a1) {
    int n = atoi(a1);
    if (n < 1024 || n > NMAX || (n & 1)) { Serial.println("ERR n even, 1024..16384"); return; }
    drainMs(100);                       // discard frames captured while the previous command ran
    doCapture(n);
  }
  else if (!strcmp(cmd, "GET") && a1) { doGet(atoi(a1)); }
  else { Serial.println("ERR unknown command"); }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_A0, OUTPUT);
  pinMode(PIN_A1, OUTPUT);
  setGain(2);
  raw = (uint16_t *)malloc(NMAX * sizeof(uint16_t));
  if (!raw) { Serial.println("ERR out of memory"); while (1) delay(1000); }
  adcStart();
  srcOff();
  delay(500);
  Serial.printf("READY %s\n", FW_VERSION);
}

void loop() {
  static char line[64];
  static int len = 0;
  if (!Serial.available()) drainMs(2);   // idle: keep the ADC DMA buffer from overflowing
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      line[len] = 0;
      handleLine(line);
      len = 0;
    } else if (c != '\r' && len < (int)sizeof(line) - 1) {
      line[len++] = c;
    }
  }
}
