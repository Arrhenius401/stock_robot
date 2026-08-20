"""企业微信应用消息推送后端 — markdown 消息，access_token 缓存 7200s"""
import logging
import time

import requests

logger = logging.getLogger(__name__)

TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
TOKEN_TTL = 7200


class WeComBackend:
    """企业微信应用消息后端（msgtype=markdown）"""

    name = "wecom"

    def __init__(self, cfg: dict):
        self._corp_id = str(cfg.get("corp_id") or "")
        self._agent_id = str(cfg.get("agent_id") or "")
        self._secret = str(cfg.get("secret") or "")
        self._to_user = str(cfg.get("to_user") or "@all")
        self._token: str | None = None
        self._token_expire_at: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expire_at:
            return self._token
        resp = requests.get(TOKEN_URL, params={
            "corpid": self._corp_id, "corpsecret": self._secret,
        }, timeout=30)
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"企业微信获取 token 失败: {data.get('errmsg')}")
        token = data["access_token"]
        self._token = token
        self._token_expire_at = time.time() + TOKEN_TTL
        return token

    def send(self, *, title: str, content: str,
             content_type: str = "markdown") -> None:
        if not (self._corp_id and self._agent_id and self._secret):
            raise ValueError("企业微信配置缺失：corp_id/agent_id/secret")
        body = {
            "touser": self._to_user,
            "msgtype": "markdown",
            "agentid": int(self._agent_id),
            "markdown": {"content": f"### {title}\n{content}"},
        }
        data = self._post(body)
        # token 失效（40014/42001）重取一次
        if data.get("errcode") in (40014, 42001):
            self._token = None
            self._token_expire_at = 0.0
            data = self._post(body)
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"企业微信发送失败: {data.get('errmsg')}")
        logger.info("企业微信消息已发送至 %s: %s", self._to_user, title)

    def _post(self, body: dict) -> dict:
        try:
            resp = requests.post(SEND_URL, params={"access_token": self._get_token()},
                                 json=body, timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"企业微信发送失败: {e}") from e
        return resp.json()
