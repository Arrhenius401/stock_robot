"""SMTP 邮件推送后端 — 将报告排版为兼容邮件客户端的 HTML。"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

_TAG_STYLES = {
    "h1": "font-size:24px;line-height:1.4;color:#15233b;margin:0 0 18px;",
    "h2": "font-size:19px;line-height:1.5;color:#15233b;margin:28px 0 12px;padding-bottom:8px;border-bottom:1px solid #dce5f0;",
    "h3": "font-size:16px;line-height:1.5;color:#243751;margin:22px 0 10px;",
    "p": "font-size:14px;line-height:1.75;margin:10px 0;color:#263449;",
    "blockquote": "margin:14px 0;padding:8px 14px;border-left:3px solid #3979cf;background:#f3f7fc;color:#40516a;",
    "table": "width:100%;border-collapse:collapse;margin:12px 0 20px;font-size:13px;line-height:1.5;",
    "th": "border:1px solid #d6e0eb;background:#edf3fa;color:#243751;text-align:left;padding:9px 11px;font-weight:700;",
    "td": "border:1px solid #d6e0eb;color:#263449;text-align:left;padding:9px 11px;vertical-align:top;word-break:break-word;",
    "ul": "margin:10px 0 16px;padding-left:24px;",
    "ol": "margin:10px 0 16px;padding-left:24px;",
    "li": "font-size:14px;line-height:1.7;margin:5px 0;color:#263449;",
    "hr": "border:0;border-top:1px solid #dce5f0;margin:24px 0;",
    "pre": "padding:12px;background:#f3f6fa;white-space:pre-wrap;word-break:break-word;",
    "code": "font-family:Consolas,monospace;word-break:break-word;",
    "a": "color:#1764bd;text-decoration:underline;",
}
_ALLOWED_TAGS = frozenset({
    *(_TAG_STYLES), "h4", "h5", "h6", "strong", "em", "thead", "tbody",
    "tr", "br", "del", "sup", "sub",
})
_VOID_TAGS = frozenset({"br", "hr"})
_IGNORED_TAGS = frozenset({"script", "style", "iframe", "object"})


class _EmailHtmlParser(HTMLParser):
    """只保留报告需要的标签，并给邮件客户端附上行内样式。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth or tag not in _ALLOWED_TAGS:
            return
        style = _TAG_STYLES.get(tag)
        attributes = f' style="{style}"' if style else ""
        if tag == "a":
            href = next((value for key, value in attrs if key == "href"), None)
            if href and href.strip().lower().startswith(("https://", "http://")):
                attributes += f' href="{escape(href, quote=True)}"'
        if tag == "table":
            attributes += ' cellpadding="0" cellspacing="0" border="0"'
        self.parts.append(f"<{tag}{attributes}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        elif not self._ignored_depth and tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(escape(data))


def render_email_html(content: str, content_type: str = "markdown") -> str:
    """渲染带行内样式的完整邮件正文。"""
    if content_type == "markdown":
        import markdown as md

        content = md.markdown(content, extensions=["tables", "fenced_code"])
    parser = _EmailHtmlParser()
    parser.feed(content)
    parser.close()
    body = "".join(parser.parts)
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:20px;background:#f3f6fa;color:#263449;'
        'font-family:Arial,\'Microsoft YaHei\',sans-serif;">'
        '<div style="max-width:760px;margin:0 auto;padding:24px;background:#fff;'
        'border:1px solid #e1e8f0;border-radius:10px;">'
        f"{body}</div></body></html>"
    )


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
        body = render_email_html(content, content_type)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = self._user
        msg["To"] = self._to_addr
        msg.attach(MIMEText(content, "plain", "utf-8"))
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
