"""SMTP 邮件推送后端 — markdown 全文转 HTML 发送"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


class EmailBackend:
    """邮件后端：端口 465 走 SSL，其余走 STARTTLS"""

    name = "email"

    def __init__(self, cfg: dict):
        self._host = str(cfg.get("smtp_host") or "")
        self._port = int(cfg.get("smtp_port") or 465)
        self._user = str(cfg.get("smtp_user") or "")
        self._password = str(cfg.get("smtp_password") or "")
        self._to_addr = str(cfg.get("to_addr") or "")

    def send(self, *, title: str, content: str,
             content_type: str = "markdown") -> None:
        if not (self._host and self._user and self._password and self._to_addr):
            raise ValueError("邮件配置缺失：smtp_host/smtp_user/smtp_password/to_addr")
        body = content
        if content_type == "markdown":
            import markdown as md
            body = md.markdown(content, extensions=["tables", "fenced_code"])
        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = self._user
        msg["To"] = self._to_addr
        msg.attach(MIMEText(body, "html", "utf-8"))
        if self._port == 465:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=30) as server:
                server.login(self._user, self._password)
                server.sendmail(self._user, [self._to_addr], msg.as_string())
        else:
            with smtplib.SMTP(self._host, self._port, timeout=30) as server:
                server.starttls()
                server.login(self._user, self._password)
                server.sendmail(self._user, [self._to_addr], msg.as_string())
        logger.info("邮件已发送至 %s: %s", self._to_addr, title)
