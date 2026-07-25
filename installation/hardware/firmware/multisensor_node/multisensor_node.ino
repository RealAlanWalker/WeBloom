#include <Arduino.h>
#include <HWCDC.h>
#include <MAX30105.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_mac.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include "telemetry_packet.h"

// COM9's assembled node was measured in situ: both 0x57 and 0x68 respond on
// GPIO8/GPIO9, while GPIO4/GPIO3 has no I2C devices.
constexpr int SDA_PIN = 8;
constexpr int SCL_PIN = 9;

constexpr uint8_t MAX30102_ADDRESS = 0x57;
constexpr uint8_t MPU6050_ADDRESS = 0x68;
constexpr uint8_t MPU6050_PWR_MGMT_1 = 0x6B;
constexpr uint8_t MPU6050_SMPLRT_DIV = 0x19;
constexpr uint8_t MPU6050_CONFIG = 0x1A;
constexpr uint8_t MPU6050_GYRO_CONFIG = 0x1B;
constexpr uint8_t MPU6050_ACCEL_CONFIG = 0x1C;
constexpr uint8_t MPU6050_ACCEL_XOUT_H = 0x3B;

constexpr uint16_t PPG_SENSOR_SAMPLE_RATE_HZ = 400;
constexpr uint8_t PPG_FIFO_SAMPLE_AVERAGE = 4;
constexpr uint16_t PPG_OUTPUT_SAMPLE_RATE_HZ =
    PPG_SENSOR_SAMPLE_RATE_HZ / PPG_FIFO_SAMPLE_AVERAGE;
// A MAX30102 can remain addressable on I2C while its FIFO silently stops
// advancing.  Keep context/ranging alive and reconfigure only the optical
// sensor when no samples have appeared for this interval.
constexpr uint32_t PPG_FIFO_STALL_TIMEOUT_MS = 2000;
constexpr uint32_t PPG_RECOVERY_RETRY_MS = 2000;
constexpr uint32_t RADIO_SLOT_PERIOD_MS = 100;
constexpr uint32_t RADIO_SLOT_WIDTH_MS = 42;
constexpr uint32_t RADIO_SLOT_PERSON_01_MS = 0;
constexpr uint32_t RADIO_SLOT_PERSON_40_MS = 54;
constexpr uint8_t PPG_TX_QUEUE_SIZE = 16;
constexpr uint8_t CONTEXT_TX_QUEUE_SIZE = 4;
constexpr uint8_t MAX_LOCAL_SEND_ATTEMPTS = 3;
// ESP-NOW normally invokes the send callback within a few milliseconds.  A
// missed callback must not leave a powered wearable permanently stuck in the
// `sendComplete == false` state.
constexpr uint32_t SEND_CALLBACK_TIMEOUT_MS = 1000;
#ifndef ADVENTUREX_PPG_PROFILE_SWEEP
#define ADVENTUREX_PPG_PROFILE_SWEEP 0
#endif
constexpr uint32_t PPG_PROFILE_DURATION_MS = 30000;
constexpr uint8_t PPG_PROFILE_FLAG_SHIFT = 5;
constexpr uint8_t PPG_PROFILE_FLAG_MASK = 0xE0;

struct PpgOpticalProfile {
  uint16_t adcRange;
  uint8_t ledAmplitude;
};

constexpr PpgOpticalProfile PPG_OPTICAL_PROFILES[] = {
    {16384, 0x7F},
    {8192, 0x40},
    {8192, 0x60},
    {8192, 0x7F},
    {16384, 0xA0},
    {16384, 0xC0},
};
constexpr uint8_t PPG_OPTICAL_PROFILE_COUNT =
    sizeof(PPG_OPTICAL_PROFILES) / sizeof(PPG_OPTICAL_PROFILES[0]);
static_assert(PPG_OPTICAL_PROFILE_COUNT <= 8,
              "PPG profile ID must fit context_flags bits 5..7");
constexpr uint16_t IMU_SAMPLE_RATE_HZ = 10;
constexpr uint32_t IMU_SAMPLE_INTERVAL_US = 1000000UL / IMU_SAMPLE_RATE_HZ;
constexpr uint16_t DISTANCE_STALE_MS = 2000;
constexpr float ZONE_ENTER_RSSI_DBM = -87.0f;
constexpr float ZONE_EXIT_RSSI_DBM = -90.0f;
constexpr uint8_t ZONE_ENTER_CONFIRMATIONS = 3;
constexpr uint8_t ZONE_EXIT_CONFIRMATIONS = 3;
#ifndef ADVENTUREX_ESPNOW_TX_POWER_QDBM
#define ADVENTUREX_ESPNOW_TX_POWER_QDBM 80
#endif
constexpr int8_t ESPNOW_TX_POWER_QDBM = ADVENTUREX_ESPNOW_TX_POWER_QDBM;
static_assert(ESPNOW_TX_POWER_QDBM >= 8 && ESPNOW_TX_POWER_QDBM <= 80,
              "ESP-NOW TX power must be between 2 and 20 dBm");
constexpr float RANGING_RSSI_SMOOTHING_ALPHA = 0.20f;
constexpr uint8_t RANGING_RSSI_MEDIAN_WINDOW = 11;
constexpr uint16_t RANGE_TREND_WINDOW_MS = 1500;
constexpr uint16_t RANGE_TREND_TOTAL_MS = RANGE_TREND_WINDOW_MS * 2;
constexpr uint8_t RANGE_TREND_MIN_SAMPLES_PER_WINDOW = 5;
constexpr float RANGE_TREND_DIRECTION_THRESHOLD_DB = 3.0f;
constexpr float RANGE_TREND_STABLE_THRESHOLD_DB = 1.0f;
constexpr uint8_t RANGE_TREND_CONFIRMATIONS = 3;
constexpr uint16_t RANGE_TREND_HOLD_MS = 2000;
constexpr uint8_t RANGE_TREND_HISTORY_SIZE = 40;
constexpr uint32_t SYNC_TIMEOUT_US = 5000000;
constexpr int32_t MAX_SYNC_STEP_US = 2000;
constexpr uint8_t DEVICE_ID_OVERRIDE = 0;

const uint8_t BROADCAST_ADDRESS[] = {0xFF, 0xFF, 0xFF,
                                     0xFF, 0xFF, 0xFF};
const uint8_t GATEWAY_ADDRESS[] = {0xD4, 0x05, 0x92, 0x7B, 0x86, 0x04};

struct RegisteredNode {
  uint8_t mac[6];
  uint8_t deviceId;
};

constexpr RegisteredNode REGISTERED_NODES[] = {
    {{0x44, 0xB1, 0x76, 0x01, 0xD7, 0xC8}, 1},
    {{0x44, 0xB1, 0x76, 0x08, 0x4B, 0xE8}, 40},
};

// Robust per-receiver fit from 0.47/1.15/1.7/2.2 m physical captures.
// Full statistics and source sample counts are stored in
// data/espnow_ranging_config_v3.json.
struct RangingCalibration {
  uint8_t localDeviceId;
  bool calibrated;
  float referenceRssiAtOneMeterDbm;
  float pathLossExponent;
};

// BEGIN GENERATED RANGING CONFIG
constexpr uint16_t RANGING_CALIBRATION_MIN_MM = 470;
constexpr uint16_t RANGING_CALIBRATION_MAX_MM = 2200;
constexpr uint16_t RANGING_CONFIG_VERSION = 3;
constexpr RangingCalibration RANGING_CALIBRATIONS[] = {
    {1, true, -85.962f, 1.4096f},
    {40, true, -85.442f, 2.2023f},
};
// END GENERATED RANGING CONFIG

MAX30105 ppgSensor;
HWCDC USBSerial;
uint8_t stationMac[6] = {};
uint8_t deviceId = 0;

RawPpgPacket fillingPpg = {};
SensorContextPacket fillingContext = {};
RawPpgPacket ppgTxQueue[PPG_TX_QUEUE_SIZE] = {};
SensorContextPacket contextTxQueue[CONTEXT_TX_QUEUE_SIZE] = {};
uint8_t ppgTxHead = 0;
uint8_t ppgTxTail = 0;
uint8_t ppgTxCount = 0;
uint8_t contextTxHead = 0;
uint8_t contextTxTail = 0;
uint8_t contextTxCount = 0;
bool contextImuValid = true;
bool imuAvailable = false;

union TxPacketBuffer {
  RawPpgPacket ppg;
  SensorContextPacket context;
};

enum class TxKind : uint8_t {
  None,
  Ppg,
  Context,
};

TxPacketBuffer txBuffer = {};
volatile bool sendComplete = true;
volatile bool lastSendSucceeded = true;
bool sendResultPending = false;
TxKind currentTxKind = TxKind::None;
uint8_t currentTxAttempts = 0;
bool currentPpgBroadcastPending = false;
uint32_t sendStartedAtMs = 0;

uint32_t ppgPacketSequence = 0;
uint32_t ppgSampleSequence = 0;
uint32_t contextPacketSequence = 0;
uint32_t imuSampleSequence = 0;
uint32_t nextImuSampleAtUs = 0;
uint8_t activePpgProfile = 0;
uint32_t ppgProfileSweepStartedAtMs = 0;
uint32_t lastPpgSampleAtMs = 0;
uint32_t lastPpgRecoveryAttemptAtMs = 0;
uint32_t ppgRecoveryCount = 0;

uint32_t sentPpgPackets = 0;
uint32_t sentContextPackets = 0;
uint32_t failedSends = 0;
uint32_t droppedPpgPackets = 0;
uint32_t droppedContextPackets = 0;
uint32_t retriedSends = 0;

volatile int32_t gatewayClockOffsetUs = 0;
volatile uint32_t lastSyncLocalUs = 0;
volatile bool clockSynchronized = false;

struct RangingSampleSnapshot {
  int8_t rssiDbm;
  uint8_t peerDeviceId;
  uint32_t receivedAtMs;
};

portMUX_TYPE rangingMux = portMUX_INITIALIZER_UNLOCKED;
volatile bool rangingSamplePending = false;
RangingSampleSnapshot pendingRangingSample = {};
uint32_t rangingSampleOverwrites = 0;
int8_t rssiHistory[RANGING_RSSI_MEDIAN_WINDOW] = {};
uint8_t rssiHistoryCount = 0;
uint8_t rssiHistoryIndex = 0;
float filteredRssiDbm = 0.0f;
bool hasFilteredRssi = false;
int8_t latestRssiDbm = 0;
uint8_t latestPeerDeviceId = 0;
uint16_t filteredDistanceMm = 0;
uint32_t lastValidRssiAtMs = 0;
bool hasValidDistance = false;
bool distanceExtrapolated = false;
uint8_t zoneState = ZONE_OUTSIDE;
uint8_t zoneEnterCount = 0;
uint8_t zoneExitCount = 0;
struct RangeTrendSample {
  float rssiDbm;
  uint32_t receivedAtMs;
};
RangeTrendSample rangeTrendHistory[RANGE_TREND_HISTORY_SIZE] = {};
uint8_t rangeTrendHistoryCount = 0;
uint8_t rangeTrendHistoryIndex = 0;
uint8_t rangeTrendState = RANGE_TREND_UNAVAILABLE;
uint8_t rangeTrendCandidateState = RANGE_TREND_UNAVAILABLE;
uint8_t rangeTrendCandidateCount = 0;
uint8_t rangeTrendStableCount = 0;
uint32_t lastRangeTrendEvidenceAtMs = 0;
float rangeTrendDeltaDb = 0.0f;
bool hasRangeTrendDelta = false;
int8_t wifiTxPowerQdbm = 0;
const RangingCalibration *rangingCalibration = nullptr;

void stopWithError(const char *message) {
  USBSerial.printf("{\"type\":\"error\",\"message\":\"%s\"}\n", message);
  while (true) {
    delay(1000);
  }
}

bool isClockSyncFresh(uint32_t localNowUs) {
  return clockSynchronized &&
         localNowUs - lastSyncLocalUs <= SYNC_TIMEOUT_US;
}

uint32_t synchronizedTimestampMs() {
  const uint32_t localNowUs = micros();
  const uint32_t timestampUs =
      isClockSyncFresh(localNowUs)
          ? static_cast<uint32_t>(localNowUs + gatewayClockOffsetUs)
          : localNowUs;
  return timestampUs / 1000;
}

void onDataSent(const wifi_tx_info_t *, esp_now_send_status_t status) {
  lastSendSucceeded = status == ESP_NOW_SEND_SUCCESS;
  sendComplete = true;
}

bool identifyRegisteredPeer(const uint8_t *sourceMac, uint8_t packetDeviceId,
                            uint8_t &peerDeviceId) {
  if (sourceMac == nullptr || memcmp(sourceMac, stationMac, sizeof(stationMac)) == 0) {
    return false;
  }
  for (const RegisteredNode &node : REGISTERED_NODES) {
    if (node.deviceId == packetDeviceId &&
        memcmp(sourceMac, node.mac, sizeof(node.mac)) == 0) {
      peerDeviceId = node.deviceId;
      return true;
    }
  }
  return false;
}

void onDataReceived(const esp_now_recv_info_t *info, const uint8_t *data,
                    int length) {
  if (length == sizeof(TimeSyncPacket)) {
    TimeSyncPacket sync;
    memcpy(&sync, data, sizeof(sync));
    if (sync.magic != TIME_SYNC_MAGIC || sync.version != TELEMETRY_VERSION) {
      return;
    }

    const uint32_t localReceivedUs = micros();
    const int32_t measuredOffsetUs =
        static_cast<int32_t>(sync.gatewayTimestampUs - localReceivedUs);
    if (!clockSynchronized) {
      gatewayClockOffsetUs = measuredOffsetUs;
    } else {
      int32_t correctionUs = measuredOffsetUs - gatewayClockOffsetUs;
      correctionUs = constrain(correctionUs, -MAX_SYNC_STEP_US, MAX_SYNC_STEP_US);
      gatewayClockOffsetUs += correctionUs / 4;
    }
    lastSyncLocalUs = localReceivedUs;
    clockSynchronized = true;
    return;
  }

  if (length != sizeof(RawPpgPacket) || info == nullptr ||
      info->rx_ctrl == nullptr) {
    return;
  }

  RawPpgPacket packet;
  memcpy(&packet, data, sizeof(packet));
  uint8_t peerDeviceId = 0;
  const int rssiDbm = info->rx_ctrl->rssi;
  if (packet.magic != TELEMETRY_MAGIC ||
      packet.version != TELEMETRY_VERSION || packet.sampleRateHz == 0 ||
      packet.sampleCount == 0 ||
      packet.sampleCount > PPG_SAMPLES_PER_PACKET || rssiDbm > -10 ||
      rssiDbm < -110 ||
      !identifyRegisteredPeer(info->src_addr, packet.deviceId, peerDeviceId)) {
    return;
  }

  portENTER_CRITICAL_ISR(&rangingMux);
  if (rangingSamplePending) {
    ++rangingSampleOverwrites;
  }
  pendingRangingSample.rssiDbm = static_cast<int8_t>(rssiDbm);
  pendingRangingSample.peerDeviceId = peerDeviceId;
  pendingRangingSample.receivedAtMs = millis();
  rangingSamplePending = true;
  portEXIT_CRITICAL_ISR(&rangingMux);
}

void initializeIdentity() {
  if (esp_read_mac(stationMac, ESP_MAC_WIFI_STA) != ESP_OK) {
    stopWithError("read_mac_failed");
  }

  if (DEVICE_ID_OVERRIDE != 0) {
    deviceId = DEVICE_ID_OVERRIDE;
    return;
  }

  for (const RegisteredNode &node : REGISTERED_NODES) {
    if (memcmp(stationMac, node.mac, sizeof(node.mac)) == 0) {
      deviceId = node.deviceId;
      return;
    }
  }

  uint8_t foldedMac = 0;
  for (uint8_t value : stationMac) {
    foldedMac ^= value;
  }
  deviceId = foldedMac == 0 ? 255 : foldedMac;
}

void initializeRadio() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  initializeIdentity();
  for (const RangingCalibration &candidate : RANGING_CALIBRATIONS) {
    if (candidate.localDeviceId == deviceId) {
      rangingCalibration = &candidate;
      break;
    }
  }
  if (rangingCalibration == nullptr) {
    stopWithError("ranging_profile_not_found");
  }
  if (rangingCalibration->calibrated &&
      (RANGING_CONFIG_VERSION == 0 ||
       rangingCalibration->pathLossExponent <= 0.0f)) {
    stopWithError("invalid_ranging_calibration");
  }
  if (esp_wifi_set_max_tx_power(ESPNOW_TX_POWER_QDBM) != ESP_OK) {
    stopWithError("set_wifi_tx_power_failed");
  }
  if (esp_wifi_get_max_tx_power(&wifiTxPowerQdbm) != ESP_OK) {
    stopWithError("get_wifi_tx_power_failed");
  }
  if (esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE) != ESP_OK) {
    stopWithError("set_channel_failed");
  }
  if (esp_now_init() != ESP_OK) {
    stopWithError("esp_now_init_failed");
  }
  if (esp_now_register_send_cb(onDataSent) != ESP_OK) {
    stopWithError("register_send_callback_failed");
  }
  if (esp_now_register_recv_cb(onDataReceived) != ESP_OK) {
    stopWithError("register_receive_callback_failed");
  }

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST_ADDRESS, sizeof(BROADCAST_ADDRESS));
  peer.channel = ESPNOW_CHANNEL;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) {
    stopWithError("add_peer_failed");
  }
  memcpy(peer.peer_addr, GATEWAY_ADDRESS, sizeof(GATEWAY_ADDRESS));
  if (esp_now_add_peer(&peer) != ESP_OK) {
    stopWithError("add_gateway_peer_failed");
  }
}

int8_t representativeRssi() {
  int8_t sorted[RANGING_RSSI_MEDIAN_WINDOW];
  memcpy(sorted, rssiHistory, rssiHistoryCount);
  for (uint8_t index = 1; index < rssiHistoryCount; ++index) {
    const int8_t value = sorted[index];
    uint8_t position = index;
    while (position > 0 && sorted[position - 1] > value) {
      sorted[position] = sorted[position - 1];
      --position;
    }
    sorted[position] = value;
  }
  return sorted[rssiHistoryCount / 2];
}

void appendRangeTrendSample(float rssiDbm, uint32_t receivedAtMs) {
  rangeTrendHistory[rangeTrendHistoryIndex] = {rssiDbm, receivedAtMs};
  rangeTrendHistoryIndex =
      (rangeTrendHistoryIndex + 1) % RANGE_TREND_HISTORY_SIZE;
  if (rangeTrendHistoryCount < RANGE_TREND_HISTORY_SIZE) {
    ++rangeTrendHistoryCount;
  }
}

void acceptEspNowRssi(const RangingSampleSnapshot &sample) {
  latestRssiDbm = sample.rssiDbm;
  latestPeerDeviceId = sample.peerDeviceId;
  rssiHistory[rssiHistoryIndex] = latestRssiDbm;
  rssiHistoryIndex = (rssiHistoryIndex + 1) % RANGING_RSSI_MEDIAN_WINDOW;
  if (rssiHistoryCount < RANGING_RSSI_MEDIAN_WINDOW) {
    ++rssiHistoryCount;
  }

  const float representative = representativeRssi();
  filteredRssiDbm = hasFilteredRssi
                        ? filteredRssiDbm + RANGING_RSSI_SMOOTHING_ALPHA *
                                                (representative - filteredRssiDbm)
                        : representative;
  hasFilteredRssi = true;
  lastValidRssiAtMs = sample.receivedAtMs;
  appendRangeTrendSample(filteredRssiDbm, sample.receivedAtMs);

  hasValidDistance = rangingCalibration != nullptr &&
                     rangingCalibration->calibrated &&
                     RANGING_CONFIG_VERSION != 0;
  if (!hasValidDistance) {
    filteredDistanceMm = 0;
    return;
  }

  const float distanceMeters = powf(
      10.0f,
      (rangingCalibration->referenceRssiAtOneMeterDbm - filteredRssiDbm) /
          (10.0f * rangingCalibration->pathLossExponent));
  filteredDistanceMm = static_cast<uint16_t>(
      constrain(lroundf(distanceMeters * 1000.0f), 50L, 30000L));
  distanceExtrapolated =
      filteredDistanceMm < RANGING_CALIBRATION_MIN_MM ||
      filteredDistanceMm > RANGING_CALIBRATION_MAX_MM;
}

bool dequeueRangingSample(RangingSampleSnapshot &sample) {
  bool available = false;
  portENTER_CRITICAL(&rangingMux);
  if (rangingSamplePending) {
    sample = pendingRangingSample;
    rangingSamplePending = false;
    available = true;
  }
  portEXIT_CRITICAL(&rangingMux);
  return available;
}

void resetRangingState() {
  hasFilteredRssi = false;
  hasValidDistance = false;
  rssiHistoryCount = 0;
  rssiHistoryIndex = 0;
  latestRssiDbm = 0;
  latestPeerDeviceId = 0;
  filteredRssiDbm = 0.0f;
  filteredDistanceMm = 0;
  distanceExtrapolated = false;
  zoneState = ZONE_OUTSIDE;
  zoneEnterCount = 0;
  zoneExitCount = 0;
  rangeTrendHistoryCount = 0;
  rangeTrendHistoryIndex = 0;
  rangeTrendState = RANGE_TREND_UNAVAILABLE;
  rangeTrendCandidateState = RANGE_TREND_UNAVAILABLE;
  rangeTrendCandidateCount = 0;
  rangeTrendStableCount = 0;
  lastRangeTrendEvidenceAtMs = 0;
  rangeTrendDeltaDb = 0.0f;
  hasRangeTrendDelta = false;
}

void serviceEspNowRanging() {
  RangingSampleSnapshot sample;
  if (dequeueRangingSample(sample)) {
    acceptEspNowRssi(sample);
  }
  if (hasFilteredRssi && millis() - lastValidRssiAtMs > DISTANCE_STALE_MS) {
    resetRangingState();
  }
}

void updateZoneStateForContext() {
  if (!hasFilteredRssi) {
    zoneState = ZONE_OUTSIDE;
    zoneEnterCount = 0;
    zoneExitCount = 0;
    return;
  }

  if (zoneState == ZONE_OUTSIDE) {
    zoneExitCount = 0;
    if (filteredRssiDbm >= ZONE_ENTER_RSSI_DBM) {
      if (zoneEnterCount < ZONE_ENTER_CONFIRMATIONS) {
        ++zoneEnterCount;
      }
      if (zoneEnterCount >= ZONE_ENTER_CONFIRMATIONS) {
        zoneState = ZONE_INSIDE;
        zoneEnterCount = 0;
      }
    } else {
      zoneEnterCount = 0;
    }
    return;
  }

  zoneEnterCount = 0;
  if (filteredRssiDbm <= ZONE_EXIT_RSSI_DBM) {
    if (zoneExitCount < ZONE_EXIT_CONFIRMATIONS) {
      ++zoneExitCount;
    }
    if (zoneExitCount >= ZONE_EXIT_CONFIRMATIONS) {
      zoneState = ZONE_OUTSIDE;
      zoneExitCount = 0;
    }
  } else {
    zoneExitCount = 0;
  }
}

bool calculateRangeTrendDelta(uint32_t nowMs, float &deltaDb) {
  float recentSum = 0.0f;
  float previousSum = 0.0f;
  uint8_t recentCount = 0;
  uint8_t previousCount = 0;
  for (uint8_t index = 0; index < rangeTrendHistoryCount; ++index) {
    const RangeTrendSample &sample = rangeTrendHistory[index];
    const uint32_t ageMs = nowMs - sample.receivedAtMs;
    if (ageMs <= RANGE_TREND_WINDOW_MS) {
      recentSum += sample.rssiDbm;
      ++recentCount;
    } else if (ageMs <= RANGE_TREND_TOTAL_MS) {
      previousSum += sample.rssiDbm;
      ++previousCount;
    }
  }
  if (recentCount < RANGE_TREND_MIN_SAMPLES_PER_WINDOW ||
      previousCount < RANGE_TREND_MIN_SAMPLES_PER_WINDOW) {
    return false;
  }
  deltaDb = recentSum / recentCount - previousSum / previousCount;
  return true;
}

void resetRangeTrendCandidate() {
  rangeTrendCandidateState = RANGE_TREND_UNAVAILABLE;
  rangeTrendCandidateCount = 0;
}

void updateRangeTrendForContext(uint32_t nowMs) {
  float deltaDb = 0.0f;
  if (!hasValidDistance ||
      !calculateRangeTrendDelta(nowMs, deltaDb)) {
    rangeTrendState = RANGE_TREND_UNAVAILABLE;
    resetRangeTrendCandidate();
    rangeTrendStableCount = 0;
    rangeTrendDeltaDb = 0.0f;
    hasRangeTrendDelta = false;
    return;
  }

  rangeTrendDeltaDb = deltaDb;
  hasRangeTrendDelta = true;
  uint8_t candidate = RANGE_TREND_UNAVAILABLE;
  if (deltaDb >= RANGE_TREND_DIRECTION_THRESHOLD_DB) {
    candidate = RANGE_TREND_APPROACHING;
  } else if (deltaDb <= -RANGE_TREND_DIRECTION_THRESHOLD_DB) {
    candidate = RANGE_TREND_RECEDING;
  }

  if (candidate != RANGE_TREND_UNAVAILABLE) {
    rangeTrendStableCount = 0;
    lastRangeTrendEvidenceAtMs = nowMs;
    if (rangeTrendCandidateState == candidate) {
      if (rangeTrendCandidateCount < RANGE_TREND_CONFIRMATIONS) {
        ++rangeTrendCandidateCount;
      }
    } else {
      rangeTrendCandidateState = candidate;
      rangeTrendCandidateCount = 1;
    }
    if (rangeTrendCandidateCount >= RANGE_TREND_CONFIRMATIONS) {
      rangeTrendState = candidate;
    }
    return;
  }

  resetRangeTrendCandidate();
  if (fabsf(deltaDb) <= RANGE_TREND_STABLE_THRESHOLD_DB) {
    if (rangeTrendStableCount < RANGE_TREND_CONFIRMATIONS) {
      ++rangeTrendStableCount;
    }
    if (rangeTrendStableCount >= RANGE_TREND_CONFIRMATIONS) {
      rangeTrendState = RANGE_TREND_STABLE;
    }
    return;
  }

  rangeTrendStableCount = 0;
  if (rangeTrendState == RANGE_TREND_UNAVAILABLE ||
      ((rangeTrendState == RANGE_TREND_APPROACHING ||
        rangeTrendState == RANGE_TREND_RECEDING) &&
       nowMs - lastRangeTrendEvidenceAtMs > RANGE_TREND_HOLD_MS)) {
    rangeTrendState = RANGE_TREND_STABLE;
  }
}

bool i2cDevicePresent(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

bool waitForI2cDevice(uint8_t address, uint32_t timeoutMs) {
  const uint32_t startedAtMs = millis();
  do {
    if (i2cDevicePresent(address)) {
      return true;
    }
    delay(20);
  } while (millis() - startedAtMs < timeoutMs);
  return false;
}

bool writeMpuRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU6050_ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool ppgProfileSweepEnabled() {
  return ADVENTUREX_PPG_PROFILE_SWEEP != 0 && deviceId == 40;
}

void applyPpgOpticalProfile(uint8_t profileIndex) {
  if (profileIndex >= PPG_OPTICAL_PROFILE_COUNT) {
    return;
  }
  const PpgOpticalProfile &profile = PPG_OPTICAL_PROFILES[profileIndex];
  ppgSensor.setup(profile.ledAmplitude, PPG_FIFO_SAMPLE_AVERAGE, 2,
                  PPG_SENSOR_SAMPLE_RATE_HZ, 411, profile.adcRange);
  ppgSensor.setPulseAmplitudeRed(profile.ledAmplitude);
  ppgSensor.setPulseAmplitudeIR(profile.ledAmplitude);
  ppgSensor.setPulseAmplitudeGreen(0);
  ppgSensor.clearFIFO();
  fillingPpg.sampleCount = 0;
  activePpgProfile = profileIndex;
  lastPpgSampleAtMs = millis();
}

void recoverStalledPpg(uint32_t nowMs) {
  if (nowMs - lastPpgSampleAtMs < PPG_FIFO_STALL_TIMEOUT_MS ||
      nowMs - lastPpgRecoveryAttemptAtMs < PPG_RECOVERY_RETRY_MS) {
    return;
  }
  lastPpgRecoveryAttemptAtMs = nowMs;
  if (!i2cDevicePresent(MAX30102_ADDRESS)) {
    return;
  }
  // begin() performs the MAX30102 soft reset/part check; setup() then restores
  // the exact production sampling profile.  Sequence counters intentionally
  // continue so the gateway and desktop can see a gap instead of a fake node
  // restart.
  if (!ppgSensor.begin(Wire, I2C_SPEED_STANDARD)) {
    return;
  }
  applyPpgOpticalProfile(activePpgProfile);
  ++ppgRecoveryCount;
}

void servicePpgProfileSweep() {
  if (!ppgProfileSweepEnabled()) {
    return;
  }
  const uint32_t elapsedMs = millis() - ppgProfileSweepStartedAtMs;
  const uint8_t requestedProfile = static_cast<uint8_t>(min<uint32_t>(
      elapsedMs / PPG_PROFILE_DURATION_MS, PPG_OPTICAL_PROFILE_COUNT - 1));
  if (requestedProfile != activePpgProfile) {
    applyPpgOpticalProfile(requestedProfile);
  }
}

void initializeSensors() {
  // Release any stale controller state left by startup before taking ownership
  // of the shared MAX30102/MPU6050 bus. The COM9 diagnostic sketch only
  // detected both devices reliably after this explicit bus reset.
  Wire.end();
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  delay(100);

  if (!waitForI2cDevice(MAX30102_ADDRESS, 2000)) {
    stopWithError("max30102_address_not_found");
  }

  if (!ppgSensor.begin(Wire, I2C_SPEED_STANDARD)) {
    stopWithError("max30102_begin_failed");
  }
  applyPpgOpticalProfile(0);
  ppgProfileSweepStartedAtMs = millis();

  imuAvailable = i2cDevicePresent(MPU6050_ADDRESS);
  if (imuAvailable) {
    imuAvailable = writeMpuRegister(MPU6050_PWR_MGMT_1, 0x00);
    delay(100);
    imuAvailable = imuAvailable && writeMpuRegister(MPU6050_CONFIG, 0x03);
    imuAvailable =
        imuAvailable && writeMpuRegister(MPU6050_SMPLRT_DIV, 19);
    imuAvailable =
        imuAvailable && writeMpuRegister(MPU6050_GYRO_CONFIG, 0x00);
    imuAvailable =
        imuAvailable && writeMpuRegister(MPU6050_ACCEL_CONFIG, 0x00);
  }
}

int16_t readBigEndianInt16() {
  const uint16_t high = static_cast<uint16_t>(Wire.read());
  const uint16_t low = static_cast<uint16_t>(Wire.read());
  return static_cast<int16_t>((high << 8) | low);
}

bool readImuSample(ImuSample &sample) {
  if (!imuAvailable) {
    return false;
  }
  Wire.beginTransmission(MPU6050_ADDRESS);
  Wire.write(MPU6050_ACCEL_XOUT_H);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }
  const size_t received = Wire.requestFrom(
      static_cast<int>(MPU6050_ADDRESS), 14, static_cast<int>(true));
  if (received != 14 || Wire.available() != 14) {
    while (Wire.available()) {
      Wire.read();
    }
    return false;
  }

  sample.accelX = readBigEndianInt16();
  sample.accelY = readBigEndianInt16();
  sample.accelZ = readBigEndianInt16();
  readBigEndianInt16();  // Chip temperature; not body temperature.
  sample.gyroX = readBigEndianInt16();
  sample.gyroY = readBigEndianInt16();
  sample.gyroZ = readBigEndianInt16();
  return true;
}

void finalizeContextPacket() {
  fillingContext.flags = SENSOR_FLAG_DISTANCE_ESPNOW_RSSI;
  if (ppgProfileSweepEnabled()) {
    fillingContext.flags |= static_cast<uint8_t>(
        (activePpgProfile << PPG_PROFILE_FLAG_SHIFT) & PPG_PROFILE_FLAG_MASK);
  }
  if (contextImuValid) {
    fillingContext.flags |= SENSOR_FLAG_IMU_VALID;
  }

  const uint32_t nowMs = millis();
  const bool hasFreshRssi =
      hasFilteredRssi && nowMs - lastValidRssiAtMs <= DISTANCE_STALE_MS;
  if (!hasFreshRssi) {
    resetRangingState();
  }
  updateZoneStateForContext();
  updateRangeTrendForContext(nowMs);

  fillingContext.rangingTxPowerQdbm =
      static_cast<uint8_t>(wifiTxPowerQdbm);
  fillingContext.rangingConfigVersion = RANGING_CONFIG_VERSION;
  if (hasFreshRssi) {
    fillingContext.rangingPeerDeviceId = latestPeerDeviceId;
    fillingContext.rangingRssiRawDbm = latestRssiDbm;
    fillingContext.rangingRssiFilteredDbm = static_cast<int8_t>(
        constrain(lroundf(filteredRssiDbm), -110L, -10L));
    fillingContext.distanceAgeMs = static_cast<uint16_t>(
        min<uint32_t>(nowMs - lastValidRssiAtMs, UINT16_MAX));
  } else {
    fillingContext.rangingPeerDeviceId = 0;
    fillingContext.rangingRssiRawDbm = 0;
    fillingContext.rangingRssiFilteredDbm = 0;
    fillingContext.distanceAgeMs = UINT16_MAX;
  }

  if (hasFreshRssi && hasValidDistance) {
    fillingContext.flags |= SENSOR_FLAG_DISTANCE_VALID;
    fillingContext.distanceMm = filteredDistanceMm;
    if (distanceExtrapolated) {
      fillingContext.flags |= SENSOR_FLAG_DISTANCE_EXTRAPOLATED;
    }
  } else {
    fillingContext.distanceMm = 0;
  }
  if (isClockSyncFresh(micros())) {
    fillingContext.flags |= SENSOR_FLAG_CLOCK_SYNCED;
  }
  fillingContext.zoneState = zoneState;
  fillingContext.rangeTrendState = rangeTrendState;
  fillingContext.rangeTrendDeltaCentiDb =
      hasRangeTrendDelta
          ? static_cast<int16_t>(constrain(
                lroundf(rangeTrendDeltaDb * 100.0f), -32767L, 32767L))
          : 0;

  if (!contextImuValid && !hasFreshRssi) {
    fillingContext.sampleCount = 0;
    contextImuValid = true;
    return;
  }

  if (contextTxCount == CONTEXT_TX_QUEUE_SIZE) {
    ++droppedContextPackets;
  } else {
    contextTxQueue[contextTxHead] = fillingContext;
    contextTxHead = (contextTxHead + 1) % CONTEXT_TX_QUEUE_SIZE;
    ++contextTxCount;
  }
  fillingContext.sampleCount = 0;
  contextImuValid = true;
}

void serviceImu() {
  const uint32_t nowUs = micros();
  if (static_cast<int32_t>(nowUs - nextImuSampleAtUs) < 0) {
    return;
  }

  if (fillingContext.sampleCount == 0) {
    fillingContext.magic = CONTEXT_MAGIC;
    fillingContext.version = CONTEXT_VERSION;
    fillingContext.deviceId = deviceId;
    fillingContext.imuSampleRateHz = IMU_SAMPLE_RATE_HZ;
    fillingContext.packetSequence = contextPacketSequence++;
    fillingContext.firstSampleSequence = imuSampleSequence;
    fillingContext.firstSampleTimestampMs = synchronizedTimestampMs();
    contextImuValid = true;
  }

  ImuSample sample = {};
  if (!readImuSample(sample)) {
    contextImuValid = false;
  }
  fillingContext.samples[fillingContext.sampleCount] = sample;
  ++fillingContext.sampleCount;
  ++imuSampleSequence;
  if (fillingContext.sampleCount == IMU_SAMPLES_PER_PACKET) {
    finalizeContextPacket();
  }

  nextImuSampleAtUs += IMU_SAMPLE_INTERVAL_US;
  if (static_cast<int32_t>(nowUs - nextImuSampleAtUs) >=
      static_cast<int32_t>(IMU_SAMPLE_INTERVAL_US)) {
    nextImuSampleAtUs = nowUs + IMU_SAMPLE_INTERVAL_US;
  }
}

void servicePpg() {
  ppgSensor.check();
  bool receivedSample = false;
  while (ppgSensor.available()) {
    receivedSample = true;
    if (fillingPpg.sampleCount == 0) {
      fillingPpg.packetSequence = ppgPacketSequence++;
      fillingPpg.firstSampleSequence = ppgSampleSequence;
      fillingPpg.firstSampleTimestampMs = synchronizedTimestampMs();
    }

    RawPpgSample &sample = fillingPpg.samples[fillingPpg.sampleCount];
    sample.red = ppgSensor.getFIFORed();
    sample.ir = ppgSensor.getFIFOIR();
    ++fillingPpg.sampleCount;
    ++ppgSampleSequence;
    ppgSensor.nextSample();

    if (fillingPpg.sampleCount == PPG_SAMPLES_PER_PACKET) {
      if (ppgTxCount == PPG_TX_QUEUE_SIZE) {
        ++droppedPpgPackets;
      } else {
        ppgTxQueue[ppgTxHead] = fillingPpg;
        ppgTxHead = (ppgTxHead + 1) % PPG_TX_QUEUE_SIZE;
        ++ppgTxCount;
      }
      fillingPpg.sampleCount = 0;
    }
  }
  const uint32_t nowMs = millis();
  if (receivedSample) {
    lastPpgSampleAtMs = nowMs;
  } else {
    recoverStalledPpg(nowMs);
  }
}

bool inRadioTransmitSlot() {
  if (!isClockSyncFresh(micros())) {
    return true;
  }
  const uint32_t slotStart =
      deviceId == 40 ? RADIO_SLOT_PERSON_40_MS : RADIO_SLOT_PERSON_01_MS;
  const uint32_t phase = synchronizedTimestampMs() % RADIO_SLOT_PERIOD_MS;
  return phase >= slotStart && phase < slotStart + RADIO_SLOT_WIDTH_MS;
}

void startSend(TxKind kind, const uint8_t *destination, const uint8_t *data,
               size_t length) {
  currentTxKind = kind;
  sendComplete = false;
  lastSendSucceeded = false;
  sendResultPending = true;
  sendStartedAtMs = millis();
  const esp_err_t result = esp_now_send(destination, data, length);
  if (result != ESP_OK) {
    sendComplete = true;
    lastSendSucceeded = false;
  }
}

void serviceRadio() {
  if (!sendComplete) {
    if (millis() - sendStartedAtMs < SEND_CALLBACK_TIMEOUT_MS) {
      return;
    }
    // Treat a lost callback exactly like a failed transmission so the normal
    // retry/drop path can advance the queue instead of wedging forever.
    lastSendSucceeded = false;
    sendComplete = true;
  }

  if (sendResultPending) {
    if (lastSendSucceeded) {
      if (currentTxKind == TxKind::Ppg) {
        ++sentPpgPackets;
        if (currentPpgBroadcastPending) {
          currentPpgBroadcastPending = false;
          ++retriedSends;
          sendResultPending = false;
          startSend(TxKind::Ppg, BROADCAST_ADDRESS,
                    reinterpret_cast<const uint8_t *>(&txBuffer.ppg),
                    sizeof(txBuffer.ppg));
          return;
        }
        ppgTxTail = (ppgTxTail + 1) % PPG_TX_QUEUE_SIZE;
        --ppgTxCount;
      } else if (currentTxKind == TxKind::Context) {
        ++sentContextPackets;
        contextTxTail = (contextTxTail + 1) % CONTEXT_TX_QUEUE_SIZE;
        --contextTxCount;
      }
      currentTxAttempts = 0;
      currentPpgBroadcastPending = false;
    } else {
      ++failedSends;
      if (currentTxAttempts < MAX_LOCAL_SEND_ATTEMPTS && inRadioTransmitSlot()) {
        ++currentTxAttempts;
        ++retriedSends;
        sendResultPending = false;
        if (currentTxKind == TxKind::Ppg) {
          startSend(TxKind::Ppg,
                    currentPpgBroadcastPending ? GATEWAY_ADDRESS
                                               : BROADCAST_ADDRESS,
                    reinterpret_cast<const uint8_t *>(&txBuffer.ppg),
                    sizeof(txBuffer.ppg));
        } else {
          startSend(TxKind::Context, GATEWAY_ADDRESS,
                    reinterpret_cast<const uint8_t *>(&txBuffer.context),
                    sizeof(txBuffer.context));
        }
        return;
      }
      // A permanently blocked queue is worse than one explicit sequence gap.
      if (currentTxKind == TxKind::Ppg && ppgTxCount > 0) {
        ppgTxTail = (ppgTxTail + 1) % PPG_TX_QUEUE_SIZE;
        --ppgTxCount;
        ++droppedPpgPackets;
      } else if (currentTxKind == TxKind::Context && contextTxCount > 0) {
        contextTxTail = (contextTxTail + 1) % CONTEXT_TX_QUEUE_SIZE;
        --contextTxCount;
        ++droppedContextPackets;
      }
      currentTxAttempts = 0;
      currentPpgBroadcastPending = false;
    }
    sendResultPending = false;
    currentTxKind = TxKind::None;
  }

  if (!inRadioTransmitSlot()) {
    return;
  }

  if (ppgTxCount > 0) {
    txBuffer.ppg = ppgTxQueue[ppgTxTail];
    currentTxAttempts = 1;
    currentPpgBroadcastPending = true;
    startSend(TxKind::Ppg, GATEWAY_ADDRESS,
              reinterpret_cast<const uint8_t *>(&txBuffer.ppg),
              sizeof(txBuffer.ppg));
    return;
  }

  if (contextTxCount > 0) {
    txBuffer.context = contextTxQueue[contextTxTail];
    currentTxAttempts = 1;
    startSend(TxKind::Context, GATEWAY_ADDRESS,
              reinterpret_cast<const uint8_t *>(&txBuffer.context),
              sizeof(txBuffer.context));
  }
}

void initializePackets() {
  fillingPpg.magic = TELEMETRY_MAGIC;
  fillingPpg.version = TELEMETRY_VERSION;
  fillingPpg.deviceId = deviceId;
  fillingPpg.sampleRateHz = PPG_OUTPUT_SAMPLE_RATE_HZ;
}

void setup() {
  USBSerial.begin(115200);
  delay(1000);

  initializeRadio();
  delay(100);
  initializeSensors();
  initializePackets();

  const uint32_t nowUs = micros();
  nextImuSampleAtUs = nowUs + IMU_SAMPLE_INTERVAL_US;

  USBSerial.printf(
      "{\"type\":\"status\",\"role\":\"multisensor_node\","
      "\"device_id\":%u,\"ppg_rate_hz\":%u,\"imu_rate_hz\":%u,"
      "\"distance_source\":\"espnow_rssi\","
      "\"ranging_calibrated\":%s,\"ranging_config_version\":%u,"
      "\"ranging_tx_power_requested_qdbm\":%d,"
      "\"ranging_tx_power_readback_qdbm\":%d,"
      "\"ranging_filter_window\":%u,\"ranging_filter_alpha\":0.20,"
      "\"ranging_calibration_min_mm\":%u,"
      "\"ranging_calibration_max_mm\":%u,"
      "\"range_trend_window_ms\":%u,"
      "\"range_trend_direction_threshold_db\":3.0,"
      "\"zone_enter_rssi_dbm\":%.1f,\"zone_exit_rssi_dbm\":%.1f,"
      "\"ranging_stale_ms\":%u,"
      "\"channel\":%u,"
      "\"max_part_id\":%u,"
      "\"imu_found\":%s,"
      "\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}\n",
      deviceId, PPG_OUTPUT_SAMPLE_RATE_HZ, IMU_SAMPLE_RATE_HZ,
      rangingCalibration->calibrated ? "true" : "false",
      RANGING_CONFIG_VERSION, ESPNOW_TX_POWER_QDBM, wifiTxPowerQdbm,
      RANGING_RSSI_MEDIAN_WINDOW, RANGING_CALIBRATION_MIN_MM,
      RANGING_CALIBRATION_MAX_MM, RANGE_TREND_WINDOW_MS,
      ZONE_ENTER_RSSI_DBM, ZONE_EXIT_RSSI_DBM, DISTANCE_STALE_MS,
      ESPNOW_CHANNEL, ppgSensor.readPartID(), imuAvailable ? "true" : "false",
      stationMac[0],
      stationMac[1], stationMac[2], stationMac[3], stationMac[4],
      stationMac[5]);
}

void loop() {
  serviceRadio();
  serviceEspNowRanging();
  servicePpgProfileSweep();
  servicePpg();
  serviceImu();
  serviceEspNowRanging();
  servicePpg();
  serviceRadio();
}
