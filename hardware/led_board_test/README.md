# IS31FL3731 灯板测试

适用开发板：ESP32-S3-N16R8。实际烧录使用 MicroPython 的 Octal PSRAM 固件，`main.py` 是上电自动运行的测试程序。

| 灯板 | ESP32-S3 |
|---|---|
| VCC | 3V3（确认灯板支持 3.3V，供电电流足够） |
| GND | GND |
| SDA | GPIO21 |
| SCL | GPIO18 |

程序以 100kHz 扫描 I²C，寻找唯一的 0x74～0x77 设备。初始化第一帧后，循环执行全板低亮度闪烁三次、144 通道逐个点亮、两次低亮度呼吸。通道扫描顺序取决于灯板布线，不保证按行列排列。PWM 上限为 16/255。

串口波特率为 115200。若未找到地址，检查供电、共地、SDA/SCL 和上拉电阻，修正后按 RESET。若需要停止，串口 REPL 中按 Ctrl+C，程序会尝试关闭驱动；按 RESET 恢复测试。

## 上传程序

烧录 MicroPython 后，可以使用 `mpremote` 更新测试程序；串口名需按实际连接修改：

```sh
mpremote connect /dev/cu.usbserial-110 fs cp hardware/led_board_test/main.py :main.py
mpremote connect /dev/cu.usbserial-110 reset
```

`led_board_test.ino` 是相同测试逻辑的 Arduino 版本，尚未编译或烧录。本次电脑缺少 Arduino 工具链，改用 MicroPython 完成测试。

## 原程序备份

烧录前的完整 16MB Flash 备份保存在 `hardware/backups/esp32s3-before-led-test.bin`，不应提交到版本库或公开分享，可能包含原固件保存的数据。恢复时使用：

```sh
esptool --chip esp32s3 --port /dev/cu.usbserial-110 --baud 460800 write_flash 0 hardware/backups/esp32s3-before-led-test.bin
```

固件来源：[MicroPython 官方 ESP32-S3 下载页](https://micropython.org/download/ESP32_GENERIC_S3/)，使用 Support for Octal-SPIRAM 的稳定版本。寄存器依据：[IS31FL3731 数据手册](https://cdn-shop.adafruit.com/product-files/4671/4671-ds.pdf)。

## 竖向位置映射（2026-10-02 实机定位）

正面看灯板，宽9列、高16行。使用1起始行列号，左上角为第1行第1列。
已观察通道0=16-1、1=15-1、8=8-1、17=15-2、143=1-9；每列从下往上连续16个通道，再从左往右换列。
物理通道 = (列号 - 1) × 16 + (16 - 行号)。暗点位于16-2和1-8，分别为通道16与127，矩阵火焰驱动会屏蔽这两点。

临时定位单颗LED（不写入文件或改配对）：

```sh
.venv/bin/python hardware/led_board_test/locate.py --port /dev/cu.usbserial-10 --channels 17
```

定位会中断当前程序。测试结束后重启开发板恢复已安装程序。APP需保持虚拟模式以释放串口。
