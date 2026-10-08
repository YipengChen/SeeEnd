"""Persistent connections are keyed by physical board identity, not model."""
from aiflame.devices.models import DEFAULT_MODEL, CONNECTION_KEYS, hardware_model
from aiflame.devices.light_settings import light_settings


def migrate_devices(config):
    if "devices" in config:
        return config["devices"]
    records = {}
    for model_id, saved in config.get("hardware_profiles", {}).items():
        if saved.get("device_id"):
            records[saved["device_id"]] = {key: value for key, value in saved.items() if key in CONNECTION_KEYS}
            records[saved["device_id"]]["hardware_model"] = model_id
    # The former active record is the newest copy; preserve explicit pause.
    if config.get("device_id"):
        records[config["device_id"]] = {key: config[key] for key in CONNECTION_KEYS if key in config}
        records[config["device_id"]]["hardware_model"] = config.get("hardware_model", DEFAULT_MODEL)
    config["devices"] = records
    return records


def public_record(record, state):
    model = hardware_model(record.get("hardware_model", DEFAULT_MODEL))
    return {"device_id": record.get("device_id", ""), "hardware_model": model["id"],
            "name": model["name"], "selected_port": record.get("port", ""),
            "transport": record.get("transport", "usb"),
            "connection_paused": bool(record.get("connection_paused")),
            "light_settings": light_settings(record), **state}
