#include <Arduino.h>
#include <Wire.h>

// ESP32-S3: SDA -> GPIO21, SCL -> GPIO18, common GND.
constexpr int SDA_PIN = 21;
constexpr int SCL_PIN = 18;
constexpr uint8_t MAX_BRIGHTNESS = 16;  // 16/255 for the first power test.
uint8_t driverAddress = 0;

bool writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(driverAddress);
  Wire.write(reg);
  Wire.write(value);
  uint8_t error = Wire.endTransmission();
  if (error) Serial.printf("I2C write failed: reg=0x%02X error=%u\n", reg, error);
  return error == 0;
}

bool fillRegisters(uint8_t first, uint16_t count, uint8_t value) {
  while (count) {
    uint8_t chunk = count > 16 ? 16 : count;
    Wire.beginTransmission(driverAddress);
    Wire.write(first);
    for (uint8_t i = 0; i < chunk; ++i) Wire.write(value);
    uint8_t error = Wire.endTransmission();
    if (error) {
      Serial.printf("I2C block failed: reg=0x%02X error=%u\n", first, error);
      return false;
    }
    first += chunk;
    count -= chunk;
  }
  return true;
}

void stopOnError() {
  // Best effort: switch off the driver if the bus still responds.
  writeRegister(0xFD, 0x0B);
  writeRegister(0x0A, 0x00);
  Serial.println("Stopped. Check power, common GND, SDA=21, SCL=18; press RESET to retry.");
  while (true) delay(1000);
}

bool initializeDriver() {
  if (!writeRegister(0xFD, 0x0B) || !writeRegister(0x0A, 0x00)) return false;
  delay(10);
  if (!writeRegister(0x00, 0x00) ||  // Picture mode
      !writeRegister(0x01, 0x00) ||  // Display frame 1
      !writeRegister(0x05, 0x00) ||  // Blink off
      !writeRegister(0x06, 0x00) ||  // Audio off
      !writeRegister(0x09, 0x00) ||  // Hardware breathing off
      !writeRegister(0xFD, 0x00) ||
      !fillRegisters(0x00, 18, 0xFF) ||
      !fillRegisters(0x12, 18, 0x00) ||
      !fillRegisters(0x24, 144, 0x00) ||
      !writeRegister(0xFD, 0x0B) ||
      !writeRegister(0x0A, 0x01) ||
      !writeRegister(0xFD, 0x00)) return false;
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("\nIS31FL3731 LED test: SDA=21 SCL=18, PWM limit=16/255");
  Wire.begin(SDA_PIN, SCL_PIN, 100000);
  Wire.setTimeOut(100);
  uint8_t candidates = 0;
  for (uint8_t address = 1; address < 127; ++address) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.printf("I2C device found: 0x%02X\n", address);
      if (address >= 0x74 && address <= 0x77) {
        driverAddress = address;
        ++candidates;
      }
    }
  }
  if (candidates != 1) {
    Serial.printf("Expected one IS31FL3731 at 0x74-0x77, found %u.\n", candidates);
    Serial.println("Check wiring/power and connect only one LED board, then press RESET.");
    while (true) delay(1000);
  }
  Serial.printf("Using IS31FL3731 address 0x%02X\n", driverAddress);
  if (!initializeDriver()) stopOnError();
}

void loop() {
  Serial.println("Test 1: all LEDs blink three times at low brightness");
  for (int i = 0; i < 3; ++i) {
    if (!fillRegisters(0x24, 144, MAX_BRIGHTNESS)) stopOnError();
    delay(400);
    if (!fillRegisters(0x24, 144, 0)) stopOnError();
    delay(400);
  }
  Serial.println("Test 2: scan all 144 channels (physical order depends on your board)");
  for (uint16_t channel = 0; channel < 144; ++channel) {
    if (!writeRegister(0x24 + channel, MAX_BRIGHTNESS)) stopOnError();
    delay(60);
    if (!writeRegister(0x24 + channel, 0)) stopOnError();
  }
  Serial.println("Test 3: low-brightness breathing");
  for (int cycle = 0; cycle < 2; ++cycle) {
    for (int brightness = 0; brightness <= MAX_BRIGHTNESS; ++brightness) {
      if (!fillRegisters(0x24, 144, brightness)) stopOnError();
      delay(50);
    }
    for (int brightness = MAX_BRIGHTNESS - 1; brightness >= 0; --brightness) {
      if (!fillRegisters(0x24, 144, brightness)) stopOnError();
      delay(50);
    }
  }
  delay(1000);
}
