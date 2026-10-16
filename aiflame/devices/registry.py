"""Persistent connections are keyed by physical board identity, not model."""
from aiflame.devices.models import DEFAULT_MODEL, SUPPORTED_MODELS, CONNECTION_KEYS, hardware_model
from aiflame.devices.light_settings import light_settings


def migrate_devices(config):
    if config.get("hardware_model") not in SUPPORTED_MODELS:
        if "hardware_model" in config:
            config["hardware_model"] = DEFAULT_MODEL
        for key in CONNECTION_KEYS:
            config.pop(key, None)
    if "devices" in config:
        records = config["devices"]
        for key in list(records):
            if records[key].get("hardware_model") not in SUPPORTED_MODELS:
                records.pop(key)
        return config["devices"]
    records = {}
    for model_id, saved in config.get("hardware_profiles", {}).items():
        if model_id in SUPPORTED_MODELS and saved.get("device_id"):
            records[saved["device_id"]] = {key: value for key, value in saved.items() if key in CONNECTION_KEYS}
            records[saved["device_id"]]["hardware_model"] = model_id
    # The former active record is the newest copy; preserve explicit pause.
    if config.get("device_id") and config.get("hardware_model") in SUPPORTED_MODELS:
        records[config["device_id"]] = {key: config[key] for key in CONNECTION_KEYS if key in config}
        records[config["device_id"]]["hardware_model"] = config.get("hardware_model", DEFAULT_MODEL)
    config["devices"] = records
    return records


def public_record(record, state):
    model = hardware_model(record.get("hardware_model", DEFAULT_MODEL))
    return {"device_id": record.get("device_id", ""), "hardware_model": model["id"],
            "name": model["name"], "selected_port": record.get("port", ""),
            "transport": record.get("transport", "yeelight"),
            "connection_paused": bool(record.get("connection_paused")),
            "light_settings": light_settings(record), **state}
