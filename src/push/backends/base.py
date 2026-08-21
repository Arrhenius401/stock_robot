"""推送后端协议与工厂"""
from typing import Literal, Protocol


class PushBackend(Protocol):
    """推送渠道接口 — content 统一为 markdown，email 后端内部转 HTML"""
    name: str

    def send(self, *, title: str, content: str,
             content_type: Literal["html", "markdown"]) -> None: ...


def get_backend(channel: str, config) -> PushBackend:
    """按渠道构建后端；未知渠道或配置缺失抛 ValueError"""
    push_cfg = config.get("push") or {}
    if channel == "email":
        from push.backends.email import EmailBackend
        return EmailBackend(push_cfg.get("email") or {})
    if channel == "wecom":
        from push.backends.wecom import WeComBackend
        return WeComBackend(push_cfg.get("wecom") or {})
    raise ValueError(f"未知推送渠道: {channel}")
