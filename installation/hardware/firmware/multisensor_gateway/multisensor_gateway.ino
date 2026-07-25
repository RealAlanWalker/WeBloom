#include <Arduino.h>
#include <HWCDC.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include "telemetry_packet.h"

constexpr size_t PACKET_QUEUE_SIZE = 128;
constexpr size_t MAX_WIRELESS_PACKET_SIZE = sizeof(RawPpgPacket);
constexpr uint8_t SERIAL_FRAME_SYNC[] = {'X', 'V', 'P', '2'};
constexpr uint8_t SERIAL_FRAME_VERSION = 1;
constexpr uint8_t SERIAL_FRAME_RAW_PPG = 1;
constexpr uint8_t SERIAL_FRAME_CONTEXT = 2;
constexpr uint32_t TIME_SYNC_INTERVAL_US = 1000000;
constexpr uint32_t RADIO_SLOT_PERIOD_US = 100000;
constexpr uint32_t TIME_SYNC_PHASE_US = 48000;
constexpr uint8_t BROADCAST_ADDRESS[] = {0xFF, 0xFF, 0xFF,
                                         0xFF, 0xFF, 0xFF};

enum class PacketKind : uint8_t {
  RawPpg,
  Context,
};

struct QueuedPacket {
  PacketKind kind;
  uint16_t length;
  uint8_t data[MAX_WIRELESS_PACKET_SIZE];
  uint8_t sourceMac[6];
  uint32_t receivedAtMs;
};

struct __attribute__((packed)) SerialRawPpgPayload {
  uint8_t frameVersion;
  uint8_t frameType;
  RawPpgPacket packet;
  uint32_t gatewayTimestampMs;
  uint8_t sourceMac[6];
  uint32_t receivedPackets;
  uint32_t invalidPackets;
  uint32_t queueOverflows;
};

struct __attribute__((packed)) SerialContextPayload {
  uint8_t frameVersion;
  uint8_t frameType;
  SensorContextPacket packet;
  uint32_t gatewayTimestampMs;
  uint8_t sourceMac[6];
  uint32_t receivedPackets;
  uint32_t invalidPackets;
  uint32_t queueOverflows;
};

static_assert(sizeof(SerialRawPpgPayload) == 128,
              "SerialRawPpgPayload layout changed unexpectedly");
static_assert(sizeof(SerialContextPayload) == 120,
              "SerialContextPayload layout changed unexpectedly");

HWCDC GatewaySerial;
portMUX_TYPE queueMux = portMUX_INITIALIZER_UNLOCKED;
QueuedPacket packetQueue[PACKET_QUEUE_SIZE];
volatile size_t queueHead = 0;
volatile size_t queueTail = 0;
volatile uint32_t invalidCount = 0;
volatile uint32_t queueOverflowCount = 0;
uint32_t receivedPacketCount = 0;
uint32_t syncSequence = 0;
uint32_t nextSyncAtUs = 0;
uint32_t lastPpgSequenceByDevice[256] = {};
bool hasPpgSequenceByDevice[256] = {};

bool validateRawPpg(const uint8_t *data, int length) {
  if (length != sizeof(RawPpgPacket)) {
    return false;
  }
  RawPpgPacket packet;
  memcpy(&packet, data, sizeof(packet));
  return packet.magic == TELEMETRY_MAGIC &&
         packet.version == TELEMETRY_VERSION &&
         packet.sampleRateHz > 0 && packet.sampleCount > 0 &&
         packet.sampleCount <= PPG_SAMPLES_PER_PACKET;
}

bool validateContext(const uint8_t *data, int length) {
  if (length != sizeof(SensorContextPacket)) {
    return false;
  }
  SensorContextPacket packet;
  memcpy(&packet, data, sizeof(packet));
  return packet.magic == CONTEXT_MAGIC &&
         packet.version == CONTEXT_VERSION &&
         packet.imuSampleRateHz > 0 && packet.sampleCount > 0 &&
         packet.sampleCount <= IMU_SAMPLES_PER_PACKET &&
         packet.zoneState <= ZONE_INSIDE &&
         packet.rangeTrendState <= RANGE_TREND_RECEDING;
}

void onDataReceived(const esp_now_recv_info_t *info, const uint8_t *data,
                    int length) {
  PacketKind kind;
  if (validateRawPpg(data, length)) {
    kind = PacketKind::RawPpg;
  } else if (validateContext(data, length)) {
    kind = PacketKind::Context;
  } else {
    ++invalidCount;
    return;
  }

  portENTER_CRITICAL_ISR(&queueMux);
  if (kind == PacketKind::RawPpg) {
    RawPpgPacket packet;
    memcpy(&packet, data, sizeof(packet));
    const uint8_t deviceId = packet.deviceId;
    if (hasPpgSequenceByDevice[deviceId] &&
        packet.packetSequence == lastPpgSequenceByDevice[deviceId]) {
      portEXIT_CRITICAL_ISR(&queueMux);
      return;
    }
    lastPpgSequenceByDevice[deviceId] = packet.packetSequence;
    hasPpgSequenceByDevice[deviceId] = true;
  }
  const size_t nextHead = (queueHead + 1) % PACKET_QUEUE_SIZE;
  if (nextHead == queueTail) {
    ++queueOverflowCount;
  } else {
    QueuedPacket &queued = packetQueue[queueHead];
    queued.kind = kind;
    queued.length = static_cast<uint16_t>(length);
    memcpy(queued.data, data, static_cast<size_t>(length));
    memcpy(queued.sourceMac, info->src_addr, sizeof(queued.sourceMac));
    queued.receivedAtMs = millis();
    queueHead = nextHead;
  }
  portEXIT_CRITICAL_ISR(&queueMux);
}

bool dequeuePacket(QueuedPacket &queued) {
  bool available = false;
  portENTER_CRITICAL(&queueMux);
  if (queueTail != queueHead) {
    queued = packetQueue[queueTail];
    queueTail = (queueTail + 1) % PACKET_QUEUE_SIZE;
    available = true;
  }
  portEXIT_CRITICAL(&queueMux);
  return available;
}

uint16_t crc16Ccitt(const uint8_t *data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t index = 0; index < length; ++index) {
    crc ^= static_cast<uint16_t>(data[index]) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000)
                ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                : static_cast<uint16_t>(crc << 1);
    }
  }
  return crc;
}

void writeSerialFrame(const uint8_t *payload, size_t payloadLength) {
  const uint16_t length = static_cast<uint16_t>(payloadLength);
  const uint16_t checksum = crc16Ccitt(payload, payloadLength);
  GatewaySerial.write(SERIAL_FRAME_SYNC, sizeof(SERIAL_FRAME_SYNC));
  GatewaySerial.write(reinterpret_cast<const uint8_t *>(&length),
                      sizeof(length));
  GatewaySerial.write(payload, payloadLength);
  GatewaySerial.write(reinterpret_cast<const uint8_t *>(&checksum),
                      sizeof(checksum));
}

void writeRawPpg(const QueuedPacket &queued) {
  SerialRawPpgPayload payload = {};
  payload.frameVersion = SERIAL_FRAME_VERSION;
  payload.frameType = SERIAL_FRAME_RAW_PPG;
  memcpy(&payload.packet, queued.data, sizeof(payload.packet));
  payload.gatewayTimestampMs = queued.receivedAtMs;
  memcpy(payload.sourceMac, queued.sourceMac, sizeof(payload.sourceMac));
  payload.receivedPackets = receivedPacketCount;
  payload.invalidPackets = invalidCount;
  payload.queueOverflows = queueOverflowCount;
  writeSerialFrame(reinterpret_cast<const uint8_t *>(&payload),
                   sizeof(payload));
}

void writeContext(const QueuedPacket &queued) {
  SerialContextPayload payload = {};
  payload.frameVersion = SERIAL_FRAME_VERSION;
  payload.frameType = SERIAL_FRAME_CONTEXT;
  memcpy(&payload.packet, queued.data, sizeof(payload.packet));
  payload.gatewayTimestampMs = queued.receivedAtMs;
  memcpy(payload.sourceMac, queued.sourceMac, sizeof(payload.sourceMac));
  payload.receivedPackets = receivedPacketCount;
  payload.invalidPackets = invalidCount;
  payload.queueOverflows = queueOverflowCount;
  writeSerialFrame(reinterpret_cast<const uint8_t *>(&payload),
                   sizeof(payload));
}

void setup() {
  GatewaySerial.begin(115200);
  delay(1000);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) {
    GatewaySerial.println(
        "{\"type\":\"error\",\"message\":\"esp_now_init_failed\"}");
    while (true) {
      delay(1000);
    }
  }

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST_ADDRESS, sizeof(BROADCAST_ADDRESS));
  peer.channel = ESPNOW_CHANNEL;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) {
    GatewaySerial.println(
        "{\"type\":\"error\",\"message\":\"add_sync_peer_failed\"}");
    while (true) {
      delay(1000);
    }
  }

  esp_now_register_recv_cb(onDataReceived);
  const uint32_t nowUs = micros();
  const uint32_t phaseUs = nowUs % RADIO_SLOT_PERIOD_US;
  nextSyncAtUs = nowUs +
      ((TIME_SYNC_PHASE_US + RADIO_SLOT_PERIOD_US - phaseUs) %
       RADIO_SLOT_PERIOD_US);
  if (nextSyncAtUs == nowUs) {
    nextSyncAtUs += RADIO_SLOT_PERIOD_US;
  }
}

void loop() {
  const uint32_t nowUs = micros();
  if (static_cast<int32_t>(nowUs - nextSyncAtUs) >= 0) {
    TimeSyncPacket sync = {};
    sync.magic = TIME_SYNC_MAGIC;
    sync.version = TELEMETRY_VERSION;
    sync.sequence = syncSequence++;
    sync.gatewayTimestampUs = nowUs;
    esp_now_send(BROADCAST_ADDRESS,
                 reinterpret_cast<const uint8_t *>(&sync), sizeof(sync));
    nextSyncAtUs += TIME_SYNC_INTERVAL_US;
    if (static_cast<int32_t>(nowUs - nextSyncAtUs) >= 0) {
      nextSyncAtUs = nowUs + TIME_SYNC_INTERVAL_US;
    }
  }

  QueuedPacket queued;
  if (!dequeuePacket(queued)) {
    delay(1);
    return;
  }

  ++receivedPacketCount;
  if (queued.kind == PacketKind::RawPpg) {
    writeRawPpg(queued);
  } else {
    writeContext(queued);
  }
}
