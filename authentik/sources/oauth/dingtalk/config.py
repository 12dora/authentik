"""Shared DingTalk OAuth and directory configuration."""

from typing import Any

DINGTALK_ALLOWLIST_SCOPES = ["openid", "corpid", "Contact.User.Read"]
DINGTALK_MAX_DEPARTMENT_DEPTH = 50
DINGTALK_MAX_DEPARTMENTS = 10000
# Incremental POST /sync/ may name at most this many DingTalk userIds to force
# a fresh topapi/v2/user/get even when the user/list row matches the cache.
DINGTALK_SYNC_FORCE_USER_IDS_MAX = 200
DINGTALK_SYNC_FORCE_USER_ID_MAX_LENGTH = 128


def normalize_dingtalk_id_list(value: Any) -> list[str]:
    """Normalize DingTalk ID collections to a stable string list."""
    if not isinstance(value, list | tuple | set):
        return []
    return sorted({str(item) for item in value if item is not None})
