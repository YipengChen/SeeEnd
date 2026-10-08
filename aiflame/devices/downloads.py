"""Build the public firmware archive without pairing data or credentials."""

import io
import zipfile
from aiflame import __version__
from aiflame.devices.firmware.version import FIRMWARE_VERSION
from aiflame.devices.base_firmware import DIRECTORY, BOARD, VERSION, bundled_image
from aiflame.devices.models import DEFAULT_MODEL, hardware_model, firmware_files


def firmware_archive(model_id=DEFAULT_MODEL):
    model = hardware_model(model_id)
    files = firmware_files(model_id)
    buffer = io.BytesIO()
    image = bundled_image()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            archive.write(path, name)
        for path in (image, DIRECTORY / "manifest.json", DIRECTORY / "LICENSE"):
            archive.write(path, "base/" + path.name)
        archive.writestr(
            "README.txt",
            f"SeeEnd APP {__version__} · 灯光固件 {FIRMWARE_VERSION}\n"
            f"硬件型号：{model['name']}；适用开发板：{BOARD}，16 MB Flash / 8 MB Octal PSRAM；IS31FL3731 暖黄色 {model['width']}×{model['height']} 灯板。\n"
            f"接线：SDA=GPIO21、SCL=GPIO18、3.3V 供电并共地；正面竖向放置，{model['width']} 列×{model['height']} 行，每列通道自下而上连续排列。\n"
            f"base/{image.name}：MicroPython {VERSION} 底层固件，不是灯光程序。\n"
            "main.py、version.py、candle.py、matrix.py、matrix_map.py、wifi_protocol.py：AI Flame 灯光程序，需一起安装，不是可直接烧录的 .bin。\n\n"
            "推荐使用 APP 安装，无需解压或手动选择文件：\n"
            "1. USB 连接，切换到硬件模式，在「我的设备」选择 USB 串口。\n"
            "2. 已有 MicroPython：点击「更新灯光程序」，保留 Wi-Fi 配置。\n"
            "3. 全新板或恢复：在「初始化 / 恢复开发板」确认板型与清除配置，"
            "点击「使用内置固件初始化」，然后安装灯光程序，再按需保存 Wi-Fi 网络。\n\n"
            "手动烧录底层固件（PORT 替换为实际串口；擦除会清除所有程序和配置）：\n"
            "esptool --chip esp32s3 --port PORT erase-flash\n"
            f"esptool --chip esp32s3 --port PORT --baud 460800 write-flash 0x0 base/{image.name}\n"
            "然后回到 APP 安装灯光程序以生成配对信息，再按需保存 Wi-Fi 网络。\n"
            "本包不包含 Wi-Fi 密码、设备配对密钥或个人配置。\n"
            "底层固件来源及 SHA-256 见 base/manifest.json；许可见 base/LICENSE。\n",
        )
    return buffer.getvalue()
