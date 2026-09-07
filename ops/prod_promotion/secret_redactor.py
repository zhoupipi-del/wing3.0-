"""secret_redactor — 审计输出脱敏（PROD-SEMANTIC-BRIDGE 工具链共用）。

设计纪律：
  - 仅做字符串 / 结构层面的脱敏，不读取任何环境变量、不连接任何服务、不落盘。
  - 仓库内此前无公共脱敏实现（已 grep 确认），本文件为 ops/prod_promotion 唯一来源。
    若日后出现全局统一实现，应改为复用而非复制。
  - promotion 工具的审计日志在写盘前必须过本模块，避免连接串里的 grade7 密码进入证据文件。
"""
from __future__ import annotations

import re
from typing import Any

_URL_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][\w+.\-]*://)(?P<user>[^:/@]+)(?P<pw>:[^@/\s]*)@(?P<rest>[^?\s]+)"
)

# 命中这些键名的字段值一律脱敏（键名大小写不敏感）
_SENSITIVE_KEYS = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "access_token", "jwt_secret_key", "psk", "private_key",
    "database_url", "database_url_sync", "redis_url", "celery_broker_url",
    "celery_result_backend", "llm_api_key", "llm_api_url", "secret_key",
    "ai_approval_envelope_key", "ps_enryption_key", "psy_encryption_key",
}
_MASK = "***REDACTED***"


def redact_url(url: str) -> str:
    if not url:
        return url
    return _URL_RE.sub(
        lambda m: f"{m.group('scheme')}{m.group('user')}:{_MASK}@{m.group('rest')}",
        url,
    )


def redact_value(key: str, value: Any) -> Any:
    k = (key or "").lower().strip()
    if k in _SENSITIVE_KEYS:
        return _MASK
    if isinstance(value, str) and "@" in value and "://" in value:
        return redact_url(value)
    return value


def redact(obj: Any) -> Any:
    """递归脱敏 dict / list / tuple 中的敏感结构。"""
    if isinstance(obj, dict):
        return {k: redact_value(k, v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact(v) for v in obj)
    return obj


def redact_text(text: str) -> str:
    """对自由文本脱敏：连接串密码 + `key=value` 形式的安全字段。"""
    if not text:
        return text
    text = redact_url(text)
    text = re.sub(
        r"(?i)\b(password|secret|token|api_key|psk|jwt_secret_key|secret_key)\s*[=:]\s*\S+",
        lambda m: f"{m.group(0).split('=')[0].split(':')[0]}={_MASK}",
        text,
    )
    return text
