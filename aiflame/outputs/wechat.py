"""Nonblocking output adapter for the optional Weixin service."""


class WechatOutput:
    name = "wechat"

    def __init__(self, service):
        self.service = service

    def accept(self, event):
        return self.service.accept(event)
