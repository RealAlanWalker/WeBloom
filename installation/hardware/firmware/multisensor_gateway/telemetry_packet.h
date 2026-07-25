#pragma once

#include <Arduino.h>

constexpr uint32_t TELEMETRY_MAGIC = 0x41565832;
constexpr uint8_t TELEMETRY_VERSION = 2;
constexpr uint32_t CONTEXT_MAGIC = 0x41565843;
constexpr uint8_t CONTEXT_VERSION = 3;
constexpr uint32_t TIME_SYNC_MAGIC = 0x53565832;
constexpr uint8_t ESPNOW_CHANNEL = 1;
constexpr size_t PPG_SAMPLES_PER_PACKET = 10;
constexpr size_t IMU_SAMPLES_PER_PACKET = 5;

constexpr uint8_t SENSOR_FLAG_DISTANCE_VALID = 1U << 0;
constexpr uint8_t SENSOR_FLAG_IMU_VALID = 1U << 1;
constexpr uint8_t SENSOR_FLAG_CLOCK_SYNCED = 1U << 2;
constexpr uint8_t SENSOR_FLAG_DISTANCE_ESPNOW_RSSI = 1U << 3;
constexpr uint8_t SENSOR_FLAG_DISTANCE_EXTRAPOLATED = 1U << 4;

enum : uint8_t {
  ZONE_OUTSIDE = 0,
  ZONE_INSIDE = 1,
};

enum : uint8_t {
  RANGE_TREND_UNAVAILABLE = 0,
  RANGE_TREND_STABLE = 1,
  RANGE_TREND_APPROACHING = 2,
  RANGE_TREND_RECEDING = 3,
};

struct __attribute__((packed)) RawPpgSample {
  uint32_t red;
  uint32_t ir;
};

struct __attribute__((packed)) RawPpgPacket {
  uint32_t magic;
  uint8_t version;
  uint8_t deviceId;
  uint16_t sampleRateHz;
  uint32_t packetSequence;
  uint32_t firstSampleSequence;
  uint32_t firstSampleTimestampMs;
  uint8_t sampleCount;
  uint8_t reserved[3];
  RawPpgSample samples[PPG_SAMPLES_PER_PACKET];
};

struct __attribute__((packed)) ImuSample {
  int16_t accelX;
  int16_t accelY;
  int16_t accelZ;
  int16_t gyroX;
  int16_t gyroY;
  int16_t gyroZ;
};

struct __attribute__((packed)) SensorContextPacket {
  uint32_t magic;
  uint8_t version;
  uint8_t deviceId;
  uint16_t imuSampleRateHz;
  uint32_t packetSequence;
  uint32_t firstSampleSequence;
  uint32_t firstSampleTimestampMs;
  uint8_t sampleCount;
  uint8_t flags;
  uint8_t zoneState;
  uint8_t rangeTrendState;
  uint8_t rangingPeerDeviceId;
  int8_t rangingRssiRawDbm;
  int8_t rangingRssiFilteredDbm;
  uint8_t rangingTxPowerQdbm;
  uint16_t rangingConfigVersion;
  uint16_t distanceMm;
  uint16_t distanceAgeMs;
  int16_t rangeTrendDeltaCentiDb;
  ImuSample samples[IMU_SAMPLES_PER_PACKET];
};

struct __attribute__((packed)) TimeSyncPacket {
  uint32_t magic;
  uint8_t version;
  uint8_t reserved[3];
  uint32_t sequence;
  uint32_t gatewayTimestampUs;
};

static_assert(sizeof(RawPpgPacket) == 104,
              "RawPpgPacket layout changed unexpectedly");
static_assert(sizeof(ImuSample) == 12,
              "ImuSample layout changed unexpectedly");
static_assert(sizeof(SensorContextPacket) == 96,
              "SensorContextPacket layout changed unexpectedly");
static_assert(sizeof(TimeSyncPacket) == 16,
              "TimeSyncPacket layout changed unexpectedly");
