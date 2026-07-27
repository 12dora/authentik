"""Shared DingTalk OAuth and directory configuration."""

from typing import Any

DINGTALK_ALLOWLIST_SCOPES = ["openid", "corpid", "Contact.User.Read"]
DINGTALK_MAX_DEPARTMENT_DEPTH = 50
DINGTALK_MAX_DEPARTMENTS = 10000


def normalize_dingtalk_id_list(value: Any) -> list[str]:
    """Normalize DingTalk ID collections to a stable string list."""
    if not isinstance(value, list | tuple | set):
        return []
    return sorted({str(item) for item in value if item is not None})
