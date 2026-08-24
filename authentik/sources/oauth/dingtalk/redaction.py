"""Shared DingTalk log redaction utilities."""

from re import sub
from typing import Any

DINGTALK_SENSITIVE_DETAIL_KEYS = (
    "access_token",
    "accessToken",
    "refresh_token",
    "refreshToken",
    "id_token",
    "idToken",
    "appsecret",
    "app_secret",
    "appSecret",
    "client_secret",
    "clientSecret",
    "consumer_secret",
    "consumerSecret",
    "x-acs-dingtalk-access-token",
    "xAcsDingtalkAccessToken",
    "x-acs-dingtalk-refresh-token",
    "xAcsDingtalkRefreshToken",
)
DINGTALK_SENSITIVE_QUERY_KEYS = (*DINGTALK_SENSITIVE_DETAIL_KEYS, "authorization")
DINGTALK_SENSITIVE_DETAIL_PATTERN = "|".join(DINGTALK_SENSITIVE_DETAIL_KEYS)
DINGTALK_SENSITIVE_QUERY_PATTERN = "|".join(DINGTALK_SENSITIVE_QUERY_KEYS)


def redact_dingtalk_detail(value: Any) -> str:
    """Return a bounded DingTalk provider detail string with credentials redacted."""
    message = str(value)
    message = sub(
        rf"(?i)(['\"]?(?:{DINGTALK_SENSITIVE_DETAIL_PATTERN})['\"]?"
        r"\s*[:=]\s*['\"]?)[^'\"&\s,;}]+",
        r"\1[redacted]",
        message,
    )
    message = sub(
        r"(?i)(['\"]?authorization['\"]?\s*[:=]\s*['\"]?)(?:Bearer\s+)?" r"[^'\"&\s,;}]+",
        r"\1[redacted]",
        message,
    )
    message = sub(
        rf"(?i)([?&](?:{DINGTALK_SENSITIVE_QUERY_PATTERN})=)[^&\s]+",
        r"\1[redacted]",
        message,
    )
    return message[:500]
