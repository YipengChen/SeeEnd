"""IS31FL3731 power-on test, ESP32-S3 SDA=21 and SCL=18."""
from machine import I2C, Pin
from time import sleep_ms

MAX_BRIGHTNESS = 16  # PWM 16/255; keep the first test dim.
CHANNELS = 144


class LEDBoard:
    def __init__(self, bus, address):
        self.bus = bus
        self.address = address

    def write(self, register, value):
        self.bus.writeto_mem(self.address, register, bytes((value,)))

    def fill(self, register, count, value):
        while count:
            chunk = min(count, 16)
            self.bus.writeto_mem(self.address, register, bytes((value,)) * chunk)
            register += chunk
            count -= chunk

    def shutdown(self):
        self.write(0xFD, 0x0B)
        self.write(0x0A, 0)

    def initialize(self):
        self.shutdown()
        sleep_ms(10)
        for register in (0x00, 0x01, 0x05, 0x06, 0x09):
            self.write(register, 0)
        self.write(0xFD, 0)  # Select frame 1.
        self.fill(0x00, 18, 0xFF)  # Enable all channels.
        self.fill(0x12, 18, 0)  # Disable blink masks.
        self.fill(0x24, CHANNELS, 0)  # Clear undefined frame RAM.
        self.write(0xFD, 0x0B)
        self.write(0x0A, 1)  # Leave shutdown.
        self.write(0xFD, 0)

    def test_cycle(self):
        print("Test 1: all LEDs blink three times, PWM=16/255")
        for _ in range(3):
            self.fill(0x24, CHANNELS, MAX_BRIGHTNESS)
            sleep_ms(400)
            self.fill(0x24, CHANNELS, 0)
            sleep_ms(400)
        print("Test 2: scan 144 channels; physical order depends on board wiring")
        for channel in range(CHANNELS):
            self.write(0x24 + channel, MAX_BRIGHTNESS)
            sleep_ms(60)
            self.write(0x24 + channel, 0)
        print("Test 3: low-brightness breathing")
        for _ in range(2):
            for brightness in range(MAX_BRIGHTNESS + 1):
                self.fill(0x24, CHANNELS, brightness)
                sleep_ms(50)
            for brightness in range(MAX_BRIGHTNESS - 1, -1, -1):
                self.fill(0x24, CHANNELS, brightness)
                sleep_ms(50)
        sleep_ms(1000)


def main():
    sleep_ms(1200)
    print("IS31FL3731 test: SDA=21 SCL=18, 100kHz")
    board = None
    try:
        bus = I2C(0, sda=Pin(21), scl=Pin(18), freq=100000, timeout=100000)
        devices = bus.scan()
        print("I2C devices:", [hex(address) for address in devices])
        candidates = [address for address in devices if 0x74 <= address <= 0x77]
        if len(candidates) != 1:
            print("Expected one IS31FL3731 at 0x74-0x77. Check power/GND/SDA/SCL, then RESET.")
            return
        board = LEDBoard(bus, candidates[0])
        board.initialize()
        print("IS31FL3731 initialized at", hex(candidates[0]))
        while True:
            board.test_cycle()
    except KeyboardInterrupt:
        print("Test stopped.")
    except Exception as error:
        print("LED test failed:", error)
    finally:
        if board is not None:
            try:
                board.shutdown()
            except OSError:
                pass


main()
