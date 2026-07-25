#include <Arduino.h>
#include <HWCDC.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_now.h>
#include <esp_wifi.h>

HWCDC USBSerial;

uint8_t scanBus(int sdaPin, int sclPin) {
  Wire.end();
  Wire.begin(sdaPin, sclPin);
  Wire.setClock(100000);
  delay(100);

  USBSerial.printf("i2c_scan_start sda=%d scl=%d sda_level=%d scl_level=%d\n",
                   sdaPin, sclPin, digitalRead(sdaPin), digitalRead(sclPin));
  uint8_t found = 0;
  for (uint8_t address = 1; address < 127; ++address) {
    Wire.beginTransmission(address);
    const uint8_t error = Wire.endTransmission();
    if (error == 0) {
      USBSerial.printf("i2c_device address=0x%02X\n", address);
      ++found;
    }
  }
  USBSerial.printf("i2c_scan_done found=%u sda_level=%d scl_level=%d\n", found,
                   digitalRead(sdaPin), digitalRead(sclPin));
  return found;
}

void setup() {
  USBSerial.begin(115200);
  delay(1000);

  USBSerial.println("phase=before_radio");
  scanBus(4, 3);
  scanBus(3, 4);
  scanBus(8, 9);
  scanBus(9, 8);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  USBSerial.printf("wifi_channel_result=%d\n",
                   esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE));
  USBSerial.printf("esp_now_init_result=%d\n", esp_now_init());
  esp_now_peer_info_t peer = {};
  memset(peer.peer_addr, 0xFF, sizeof(peer.peer_addr));
  peer.channel = 1;
  peer.ifidx = WIFI_IF_STA;
  USBSerial.printf("esp_now_add_peer_result=%d\n", esp_now_add_peer(&peer));
  delay(100);

  USBSerial.println("phase=after_radio");
  scanBus(4, 3);
  scanBus(3, 4);
  scanBus(8, 9);
  scanBus(9, 8);
}

void loop() {
  delay(1000);
}
