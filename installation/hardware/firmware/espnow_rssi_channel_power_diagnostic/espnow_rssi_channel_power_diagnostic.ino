#include <Arduino.h>
#include <HWCDC.h>
#include <WiFi.h>
#include <esp_mac.h>
#include <esp_now.h>
#include <esp_wifi.h>

constexpr uint32_t MAGIC = 0x58524348;
constexpr uint8_t VERSION = 1;
constexpr uint32_t PHASE_DURATION_MS = 15000;
constexpr uint32_t SEND_INTERVAL_MS = 100;
constexpr uint8_t BROADCAST_ADDRESS[] = {0xFF, 0xFF, 0xFF,
                                         0xFF, 0xFF, 0xFF};

struct RadioConfig {
  uint8_t channel;
  int8_t txPowerQdbm;
};

constexpr RadioConfig RADIO_CONFIGS[] = {
    {1, 80}, {1, 84}, {6, 80}, {6, 84}, {11, 80}, {11, 84},
};

struct RegisteredNode {
  uint8_t mac[6];
  uint8_t deviceId;
};

constexpr RegisteredNode REGISTERED_NODES[] = {
    {{0x44, 0xB1, 0x76, 0x01, 0xD7, 0xC8}, 1},
    {{0x44, 0xB1, 0x76, 0x08, 0x4B, 0xE8}, 40},
};

struct __attribute__((packed)) RadioTestPacket {
  uint32_t magic;
  uint8_t version;
  uint8_t deviceId;
  uint8_t phaseIndex;
  uint8_t channel;
  int8_t requestedPowerQdbm;
  int8_t appliedPowerQdbm;
  uint8_t reserved[2];
  uint32_t sequence;
  uint32_t uptimeMs;
};

static_assert(sizeof(RadioTestPacket) == 20,
              "RadioTestPacket layout changed");

HWCDC USBSerial;
uint8_t stationMac[6] = {};
uint8_t deviceId = 0;
uint8_t phaseIndex = 0;
int8_t appliedPowerQdbm = 0;
uint32_t phaseStartedAtMs = 0;
uint32_t nextSendAtMs = 0;
uint32_t sequence = 0;
volatile uint32_t sendFailures = 0;

void stopWithError(const char *message) {
  USBSerial.printf("{\"type\":\"error\",\"message\":\"%s\"}\n", message);
  while (true) {
    delay(1000);
  }
}

uint8_t identifyDevice() {
  for (const RegisteredNode &node : REGISTERED_NODES) {
    if (memcmp(stationMac, node.mac, sizeof(node.mac)) == 0) {
      return node.deviceId;
    }
  }
  return 0;
}

bool isRegisteredPeer(const uint8_t *sourceMac, uint8_t peerDeviceId) {
  if (sourceMac == nullptr ||
      memcmp(sourceMac, stationMac, sizeof(stationMac)) == 0) {
    return false;
  }
  for (const RegisteredNode &node : REGISTERED_NODES) {
    if (node.deviceId == peerDeviceId &&
        memcmp(sourceMac, node.mac, sizeof(node.mac)) == 0) {
      return true;
    }
  }
  return false;
}

void onDataSent(const wifi_tx_info_t *, esp_now_send_status_t status) {
  if (status != ESP_NOW_SEND_SUCCESS) {
    sendFailures = sendFailures + 1;
  }
}

void onDataReceived(const esp_now_recv_info_t *info, const uint8_t *data,
                    int length) {
  if (length != sizeof(RadioTestPacket) || info == nullptr ||
      info->rx_ctrl == nullptr) {
    return;
  }
  RadioTestPacket packet;
  memcpy(&packet, data, sizeof(packet));
  if (packet.magic != MAGIC || packet.version != VERSION ||
      packet.channel != RADIO_CONFIGS[phaseIndex].channel ||
      !isRegisteredPeer(info->src_addr, packet.deviceId)) {
    return;
  }
  USBSerial.printf(
      "{\"type\":\"espnow_channel_power_sample\","
      "\"receiver_device_id\":%u,\"sender_device_id\":%u,"
      "\"sender_phase\":%u,\"channel\":%u,\"seq\":%lu,"
      "\"requested_power_qdbm\":%d,\"applied_power_qdbm\":%d,"
      "\"rssi_dbm\":%d,\"rx_channel\":%u}\n",
      deviceId, packet.deviceId, packet.phaseIndex, packet.channel,
      static_cast<unsigned long>(packet.sequence),
      packet.requestedPowerQdbm, packet.appliedPowerQdbm,
      info->rx_ctrl->rssi, info->rx_ctrl->channel);
}

void applyPhase(uint8_t nextPhase) {
  phaseIndex = nextPhase %
               (sizeof(RADIO_CONFIGS) / sizeof(RADIO_CONFIGS[0]));
  const RadioConfig &config = RADIO_CONFIGS[phaseIndex];
  if (esp_wifi_set_channel(config.channel, WIFI_SECOND_CHAN_NONE) != ESP_OK ||
      esp_wifi_set_max_tx_power(config.txPowerQdbm) != ESP_OK ||
      esp_wifi_get_max_tx_power(&appliedPowerQdbm) != ESP_OK) {
    stopWithError("apply_radio_config_failed");
  }
  phaseStartedAtMs = millis();
  USBSerial.printf(
      "{\"type\":\"espnow_channel_power_phase\",\"device_id\":%u,"
      "\"phase\":%u,\"duration_ms\":%lu,\"channel\":%u,"
      "\"requested_power_qdbm\":%d,\"applied_power_qdbm\":%d,"
      "\"send_failures\":%lu}\n",
      deviceId, phaseIndex, static_cast<unsigned long>(PHASE_DURATION_MS),
      config.channel, config.txPowerQdbm, appliedPowerQdbm,
      static_cast<unsigned long>(sendFailures));
}

void setup() {
  USBSerial.begin(115200);
  delay(1000);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  if (esp_read_mac(stationMac, ESP_MAC_WIFI_STA) != ESP_OK) {
    stopWithError("read_mac_failed");
  }
  deviceId = identifyDevice();
  if (deviceId == 0) {
    stopWithError("unregistered_node");
  }
  if (esp_wifi_set_channel(RADIO_CONFIGS[0].channel,
                           WIFI_SECOND_CHAN_NONE) != ESP_OK ||
      esp_now_init() != ESP_OK ||
      esp_now_register_send_cb(onDataSent) != ESP_OK ||
      esp_now_register_recv_cb(onDataReceived) != ESP_OK) {
    stopWithError("esp_now_init_failed");
  }

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST_ADDRESS, sizeof(BROADCAST_ADDRESS));
  peer.channel = 0;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) {
    stopWithError("add_peer_failed");
  }
  applyPhase(0);
  nextSendAtMs = millis();
}

void loop() {
  const uint32_t nowMs = millis();
  if (nowMs - phaseStartedAtMs >= PHASE_DURATION_MS) {
    applyPhase(phaseIndex + 1);
  }
  if (static_cast<int32_t>(nowMs - nextSendAtMs) < 0) {
    delay(1);
    return;
  }

  const RadioConfig &config = RADIO_CONFIGS[phaseIndex];
  RadioTestPacket packet = {};
  packet.magic = MAGIC;
  packet.version = VERSION;
  packet.deviceId = deviceId;
  packet.phaseIndex = phaseIndex;
  packet.channel = config.channel;
  packet.requestedPowerQdbm = config.txPowerQdbm;
  packet.appliedPowerQdbm = appliedPowerQdbm;
  packet.sequence = sequence++;
  packet.uptimeMs = nowMs;
  if (esp_now_send(BROADCAST_ADDRESS,
                   reinterpret_cast<const uint8_t *>(&packet),
                   sizeof(packet)) != ESP_OK) {
    sendFailures = sendFailures + 1;
  }
  nextSendAtMs += SEND_INTERVAL_MS;
  if (static_cast<int32_t>(nowMs - nextSendAtMs) >= 0) {
    nextSendAtMs = nowMs + SEND_INTERVAL_MS;
  }
}
