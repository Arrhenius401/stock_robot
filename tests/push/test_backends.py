import pytest

from push.backends import get_backend
from push.backends.email import EmailBackend
from push.backends.wecom import WeComBackend


class TestGetBackend:
    def test_email(self):
        backend = get_backend("email", _fake_config())
        assert isinstance(backend, EmailBackend)

    def test_wecom(self):
        backend = get_backend("wecom", _fake_config())
        assert isinstance(backend, WeComBackend)

    def test_unknown_channel(self):
        with pytest.raises(ValueError):
            get_backend("sms", _fake_config())


class TestEmailBackend:
    def test_missing_config_raises(self):
        backend = EmailBackend({})
        with pytest.raises(ValueError):
            backend.send(title="t", content="c", content_type="markdown")

    def test_send_ssl_465(self, mocker):
        import email
        from email import message_from_string
        smtp_cls = mocker.patch("smtplib.SMTP_SSL")
        backend = EmailBackend(_email_cfg())
        backend.send(title="标题", content="**加粗**", content_type="markdown")
        ctx = smtp_cls.return_value.__enter__.return_value
        ctx.login.assert_called_once_with("user@qq.com", "auth-code")
        ctx.sendmail.assert_called_once()
        msg = message_from_string(ctx.sendmail.call_args.args[2])
        # 主题/正文因中文分别走 RFC2047/base64 编码，解码后再断言
        subject = str(email.header.make_header(email.header.decode_header(msg["Subject"])))
        body = msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
        assert "标题" in subject
        assert "<strong>加粗</strong>" in body  # markdown 已转 HTML

    def test_send_starttls_587(self, mocker):
        smtp_cls = mocker.patch("smtplib.SMTP")
        cfg = _email_cfg()
        cfg["smtp_port"] = 587
        backend = EmailBackend(cfg)
        backend.send(title="t", content="c", content_type="html")
        smtp_cls.return_value.__enter__.return_value.starttls.assert_called_once()


class TestWeComBackend:
    def test_missing_config_raises(self):
        backend = WeComBackend({})
        with pytest.raises(ValueError):
            backend.send(title="t", content="c", content_type="markdown")

    def test_send_with_token_cache(self, mocker):
        get = mocker.patch("requests.get", return_value=_resp({"errcode": 0, "access_token": "tok1"}))
        post = mocker.patch("requests.post", return_value=_resp({"errcode": 0}))
        backend = WeComBackend(_wecom_cfg())
        backend.send(title="标题", content="摘要", content_type="markdown")
        backend.send(title="标题2", content="摘要2", content_type="markdown")
        assert post.call_count == 2
        # 第二次发送复用缓存的 token，不再请求 gettoken
        assert get.call_count == 1

    def test_send_retries_on_token_expired(self, mocker):
        mocker.patch("requests.get", return_value=_resp({"errcode": 0, "access_token": "tok1"}))
        post = mocker.patch("requests.post", side_effect=[
            _resp({"errcode": 40014, "errmsg": "invalid token"}),
            _resp({"errcode": 0}),
        ])
        backend = WeComBackend(_wecom_cfg())
        backend.send(title="t", content="c", content_type="markdown")
        assert post.call_count == 2

    def test_send_error_raises(self, mocker):
        mocker.patch("requests.get", return_value=_resp({"errcode": 0, "access_token": "tok1"}))
        mocker.patch("requests.post", return_value=_resp({"errcode": 60020, "errmsg": "not allowed"}))
        backend = WeComBackend(_wecom_cfg())
        with pytest.raises(RuntimeError):
            backend.send(title="t", content="c", content_type="markdown")


def _fake_config():
    class _C:
        def get(self, key, default=None):
            data = {
                "push": {
                    "email": {"smtp_host": "smtp.qq.com", "smtp_port": 465,
                              "smtp_user": "user@qq.com", "smtp_password": "x",
                              "to_addr": "user@qq.com"},
                    "wecom": {"corp_id": "cid", "agent_id": "1", "secret": "sec",
                              "to_user": "@all"},
                },
            }
            node = data
            for k in key.split("."):
                node = node.get(k) if isinstance(node, dict) else None
            return node if node is not None else default
    return _C()


def _email_cfg():
    return {"smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_user": "user@qq.com",
            "smtp_password": "auth-code", "to_addr": "user@qq.com"}


def _wecom_cfg():
    return {"corp_id": "cid", "agent_id": "1", "secret": "sec", "to_user": "@all"}


def _resp(data):
    class _R:
        def json(self):
            return data
    return _R()
