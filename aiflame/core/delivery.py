"""Activity receipt explanations; device success comes only from stored ACKs."""
import json


REASONS = {
    "applied": "设备已确认接收指令",
    "queued": "等待设备确认",
    "screen": "本地灯光状态已更新，未发送到实体设备",
    "offline": "事件到达时设备离线，未收到设备回执",
    "disabled": "设备已断开连接",
    "muted": "设备亮度为 0%，未发送",
    "superseded": "后续任务状态已替代此事件，不再发送",
    "suppressed": "灯光提醒未触发，请检查亮度或应用运行状态",
    "unmatched": "来源已停用，未发送",
    "expired": "提醒已结束或事件已过期，未收到设备回执",
    "dropped": "队列已满，此事件已淘汰",
    "ended_on_reconnect": "连接已变化，旧提示不补播",
    "interrupted": "进程重启，未重播",
    "failed": "投递失败",
    "lease_expired": "工作状态超时，不代表任务完成",
}


def delivery_summary(flame, event, receipts):
    status = event["status"]
    reason = REASONS.get(status, status)
    queue_reason = ("排队中，当前完成提醒等待键鼠活动结束" if flame.current and flame.current.get("holding")
                    else "排队中，等待当前提醒结束")
    if status == "queued":
        if any(item["id"] == event["id"] for item in flame.queue):
            reason = queue_reason
        elif not flame.device.get("online"):
            reason = "没有在线设备，等待更新本地灯光状态"
        else:
            reason = "已进入提醒调度，等待设备回执"
    devices = []
    for receipt in receipts:
        if receipt["event"] != event["id"] or not receipt["output"].startswith("flame:"):
            continue
        device_id = receipt["output"][6:]
        try:
            metadata = json.loads(receipt["detail"] or "{}")
        except (ValueError, TypeError):
            metadata = {}
        state = receipt["status"]
        explanation = REASONS.get(state, state)
        output = getattr(flame.transport, "outputs", {}).get(device_id)
        record = getattr(flame.transport, "records", {}).get(device_id)
        if state == "queued":
            if not record:
                explanation = "设备已移除，未收到回执"
            elif record.get("connection_paused"):
                explanation = "设备已断开连接，未收到回执"
            elif not output or not output.device.get("online"):
                explanation = "设备已离线，未收到回执"
            elif output.settings["brightness"] == 0:
                explanation = REASONS["muted"]
            elif any(item["id"] == event["id"] for item in flame.queue):
                explanation = queue_reason
        devices.append({"device_id": device_id, "name": metadata.get("name", "设备"),
                        "status": state, "reason": explanation})
    return {"reason": reason, "devices": sorted(devices, key=lambda row: row["device_id"])}
