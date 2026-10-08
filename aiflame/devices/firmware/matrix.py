"""IS31FL3731 driver with explicit calibrated logical-to-channel mapping.

Matrix(bus, mapping, disabled_channels=(), address=None) validates a row-major
width x height mapping. Each entry is a distinct channel 0..143, or -1 for an explicitly
unmapped position. No physical board wiring is inferred. initialize() clears
both buffers. show(pixels) synchronously writes an entire hidden frame before
flipping display; failures attempt hardware shutdown and propagate.
"""
try:
    from time import sleep_ms
except ImportError:
    from time import sleep

    def sleep_ms(ms):
        sleep(ms / 1000)

CHANNELS = 144
MAX_PWM = 16


class Matrix:
    def __init__(self, bus, mapping, disabled_channels=(), address=None, width=9, height=16):
        if (width, height) not in ((9, 16), (7, 15)) or mapping is None or len(mapping) != width * height:
            raise ValueError("Calibrated mapping must match the selected display dimensions")
        seen = set()
        for channel in mapping:
            if type(channel) is not int or channel < -1 or channel >= CHANNELS:
                raise ValueError("Invalid mapped channel")
            if channel >= 0:
                if channel in seen:
                    raise ValueError("Duplicate mapped channel")
                seen.add(channel)
        disabled = set()
        for channel in disabled_channels:
            if type(channel) is not int or channel < 0 or channel >= CHANNELS or channel in disabled:
                raise ValueError("Invalid or duplicate disabled channel")
            disabled.add(channel)
        if address is None:
            candidates = [a for a in bus.scan() if 0x74 <= a <= 0x77]
            if len(candidates) != 1:
                raise ValueError("Expected one IS31FL3731 at 0x74..0x77")
            address = candidates[0]
        elif not isinstance(address, int) or not 0x74 <= address <= 0x77:
            raise ValueError("Invalid IS31FL3731 address")
        self.bus = bus
        self.address = address
        self.mapping = tuple(mapping)
        self.disabled_channels = disabled
        self._pwm = bytearray(CHANNELS)
        self._byte = bytearray(1)
        self._display_frame = 0
        self.ready = False
        self.frames_shown = 0
        self.failures = 0

    def _write(self, register, value):
        self._byte[0] = value
        self.bus.writeto_mem(self.address, register, self._byte)

    def _bank(self, bank):
        self._write(0xFD, bank)

    def shutdown(self):
        self.ready = False
        self._bank(0x0B)
        self._write(0x0A, 0)

    def _fail(self):
        self.failures += 1
        try:
            self.shutdown()
        except Exception:
            self.ready = False

    def initialize(self):
        try:
            self.shutdown()
            sleep_ms(10)
            for register in (0x00, 0x01, 0x05, 0x06, 0x09):
                self._write(register, 0)
            enabled = bytearray(18)
            for channel in self.mapping:
                if channel >= 0 and channel not in self.disabled_channels:
                    enabled[channel // 8] |= 1 << (channel % 8)
            zeros = bytearray(18)
            for i in range(CHANNELS):
                self._pwm[i] = 0
            for frame in (0, 1):
                self._bank(frame)
                self.bus.writeto_mem(self.address, 0x00, enabled)
                self.bus.writeto_mem(self.address, 0x12, zeros)
                self.bus.writeto_mem(self.address, 0x24, self._pwm)
            self._bank(0x0B)
            self._write(0x01, 0)
            self._write(0x0A, 1)
            self._display_frame = 0
            self.ready = True
        except Exception:
            self._fail()
            raise

    def show(self, pixels):
        if len(pixels) != len(self.mapping):
            raise ValueError("PWM buffer must match the logical display dimensions")
        if not self.ready:
            raise RuntimeError("Matrix is not initialized")
        for i in range(CHANNELS):
            self._pwm[i] = 0
        for logical in range(len(self.mapping)):
            channel = self.mapping[logical]
            if channel >= 0 and channel not in self.disabled_channels:
                self._pwm[channel] = min(MAX_PWM, max(0, int(pixels[logical])))
        frame = 1 - self._display_frame
        try:
            self._bank(frame)
            self.bus.writeto_mem(self.address, 0x24, self._pwm)
            self._bank(0x0B)
            self._write(0x01, frame)
            self._display_frame = frame
            self.frames_shown += 1
        except Exception:
            self._fail()
            raise
