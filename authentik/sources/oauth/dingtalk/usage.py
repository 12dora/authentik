"""DingTalk outbound API usage buckets and EasyAuth usage-policy enforcement."""

from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Any

from django.core.cache import cache
from django.db import connection
from django.utils.translation import gettext as _
from structlog.stdlib import get_logger

from authentik.sources.oauth.models import DingTalkApiUsageBucket, OAuthSource

LOGGER = get_logger()

CATEGORY_TOKEN = "ak_token"
CATEGORY_LOGIN = "ak_login"
CATEGORY_AUTH_INFO = "ak_auth_info"
CATEGORY_DIRECTORY_INCREMENTAL = "ak_directory_incremental"
CATEGORY_DIRECTORY_FULL = "ak_directory_full"
CATEGORY_ALLOWLIST = "ak_allowlist"

CATEGORY_PRIORITY: dict[str, str] = {
    CATEGORY_TOKEN: "p0",
    CATEGORY_LOGIN: "p0",
    CATEGORY_AUTH_INFO: "p1",
    CATEGORY_DIRECTORY_INCREMENTAL: "p1",
    CATEGORY_DIRECTORY_FULL: "p2",
    CATEGORY_ALLOWLIST: "p2",
}

# Per-(source, hour, priority) throttle counters live as reserved bucket rows so the
# increment is one atomic INSERT ... ON CONFLICT DO UPDATE (DatabaseCache has no incr).
THROTTLE_CATEGORY_P1 = "_throttle_p1"
THROTTLE_CATEGORY_P2 = "_throttle_p2"
THROTTLE_CATEGORIES: dict[str, str] = {
    "p1": THROTTLE_CATEGORY_P1,
    "p2": THROTTLE_CATEGORY_P2,
}
INTERNAL_USAGE_CATEGORIES: frozenset[str] = frozenset(THROTTLE_CATEGORIES.values())

USAGE_POLICY_BLOCKED_MESSAGE = "DingTalk call was blocked by usage policy."
USAGE_RETENTION_DAYS = 60
USAGE_SINCE_MAX_DAYS = 45
POLICY_MEMO_TTL_SECONDS = 30.0
_POLICY_CACHE_PREFIX = "authentik/sources/oauth/dingtalk/usage/policy"
_POLICY_MEMO: dict[str, tuple[float, dict[str, Any] | None]] = {}
_POLICY_MEMO_LOCK = Lock()
_POLICY_MEMO_MISS = object()
_BUCKET_INCREMENT_FIELDS = frozenset({"count", "blocked_count"})


class DingTalkUsagePolicyBlocked(Exception):
    """Raised when a DingTalk HTTP attempt is refused by the pushed usage policy."""

    def __init__(self, category: str = ""):
        self.category = category
        super().__init__(_(USAGE_POLICY_BLOCKED_MESSAGE))


def current_hour_start(moment: datetime | None = None) -> datetime:
    """Return ``moment`` truncated to the UTC hour."""
    value = moment or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.replace(minute=0, second=0, microsecond=0)


def format_utc_z(value: datetime) -> str:
    """Format a datetime as ISO-8601 UTC with a ``Z`` suffix."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def directory_usage_category(*, full: bool) -> str:
    """Return the directory-client category for a full or incremental sync."""
    return CATEGORY_DIRECTORY_FULL if full else CATEGORY_DIRECTORY_INCREMENTAL


def clear_policy_memo() -> None:
    """Drop the process-local usage-policy memo. Used by tests."""
    with _POLICY_MEMO_LOCK:
        _POLICY_MEMO.clear()


def _policy_cache_key(source: OAuthSource) -> str:
    return f"{_POLICY_CACHE_PREFIX}/{source.pk}"


def _policy_memo_key(source: OAuthSource) -> str:
    return str(source.pk)


def _set_policy_memo(source: OAuthSource, payload: dict[str, Any] | None) -> None:
    with _POLICY_MEMO_LOCK:
        _POLICY_MEMO[_policy_memo_key(source)] = (
            monotonic() + POLICY_MEMO_TTL_SECONDS,
            payload,
        )


def _get_policy_memo(source: OAuthSource) -> Any:
    key = _policy_memo_key(source)
    now_ts = monotonic()
    with _POLICY_MEMO_LOCK:
        entry = _POLICY_MEMO.get(key)
        if entry is None or entry[0] <= now_ts:
            _POLICY_MEMO.pop(key, None)
            return _POLICY_MEMO_MISS
        return entry[1]


def _parse_expires_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _fresh_policy(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    expires_at = _parse_expires_at(payload.get("expires_at"))
    if expires_at is None or expires_at <= datetime.now(UTC):
        return None
    return payload


def store_policy(source: OAuthSource, policy: dict[str, Any]) -> dict[str, Any]:
    """Persist the EasyAuth usage policy in the shared cache until ``expires_at``.

    The process that handles the PUT also replaces its 30 s in-memory memo
    immediately so the next check in this worker sees the new document.
    """
    expires_at = _parse_expires_at(policy.get("expires_at"))
    payload = {
        "blocked_priorities": list(policy.get("blocked_priorities") or []),
        "throttle_per_hour": dict(policy.get("throttle_per_hour") or {}),
        "block_p0_billed": bool(policy.get("block_p0_billed")),
        "expires_at": format_utc_z(expires_at) if expires_at is not None else "",
    }
    key = _policy_cache_key(source)
    if expires_at is None:
        cache.delete(key)
        _set_policy_memo(source, None)
        return payload
    ttl = int((expires_at - datetime.now(UTC)).total_seconds())
    if ttl <= 0:
        cache.delete(key)
        _set_policy_memo(source, None)
        return payload
    cache.set(key, payload, timeout=ttl)
    _set_policy_memo(source, payload)
    return payload


def _load_policy_from_cache(source: OAuthSource) -> dict[str, Any] | None:
    try:
        payload = cache.get(_policy_cache_key(source))
    except Exception as exc:  # noqa: BLE001 - missing policy must fail open
        LOGGER.warning(
            "dingtalk_usage_policy_load_failed",
            source_pk=str(source.pk),
            source_slug=source.slug,
            exception_type=type(exc).__name__,
        )
        raise
    return _fresh_policy(payload)


def _load_policy(source: OAuthSource) -> dict[str, Any] | None:
    memoized = _get_policy_memo(source)
    if memoized is not _POLICY_MEMO_MISS:
        return _fresh_policy(memoized)
    try:
        payload = _load_policy_from_cache(source)
    except Exception:  # noqa: BLE001 - infrastructure failures fail open and are not memoized
        return None
    _set_policy_memo(source, payload)
    return payload


def _increment_bucket_field(source: OAuthSource, category: str, field: str) -> int:
    """Atomically add 1 to ``field`` and return the new value.

    Single ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` with no wrapping
    ``atomic()`` of our own, so we do not extend a caller's transaction.
    """
    if field not in _BUCKET_INCREMENT_FIELDS:
        raise ValueError(f"Unsupported usage bucket field {field}.")
    hour_start = current_hour_start()
    count = 1 if field == "count" else 0
    blocked_count = 1 if field == "blocked_count" else 0
    quote = connection.ops.quote_name
    table = quote(DingTalkApiUsageBucket._meta.db_table)
    field_sql = quote(field)
    sql = (
        f"INSERT INTO {table} "
        f"({quote('source_id')}, {quote('hour_start')}, {quote('category')}, "
        f"{quote('count')}, {quote('blocked_count')}) "
        f"VALUES (%s, %s, %s, %s, %s) "
        f"ON CONFLICT ({quote('source_id')}, {quote('hour_start')}, {quote('category')}) "
        f"DO UPDATE SET {field_sql} = {table}.{field_sql} + 1 "
        f"RETURNING {field_sql}"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [source.pk, hour_start, category, count, blocked_count])
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def _increment_throttle(source: OAuthSource, priority: str) -> int:
    category = THROTTLE_CATEGORIES.get(priority)
    if category is None:
        return 0
    return _increment_bucket_field(source, category, "count")


def _throttle_limit(policy: dict[str, Any], priority: str) -> int | None:
    raw = (policy.get("throttle_per_hour") or {}).get(priority)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except TypeError, ValueError:
        return None


def _should_refuse(source: OAuthSource, category: str) -> bool:
    if category == CATEGORY_TOKEN:
        return False
    policy = _load_policy(source)
    if policy is None:
        return False
    priority = CATEGORY_PRIORITY.get(category)
    if priority is None:
        return False
    blocked_priorities = {
        str(item) for item in (policy.get("blocked_priorities") or []) if item is not None
    }
    if priority == "p0":
        return bool(category == CATEGORY_LOGIN and policy.get("block_p0_billed"))
    if priority in blocked_priorities:
        return True
    limit = _throttle_limit(policy, priority)
    if limit is None or priority not in THROTTLE_CATEGORIES:
        return False
    return _increment_throttle(source, priority) > limit


def _increment_bucket(source: OAuthSource, category: str, field: str) -> None:
    _increment_bucket_field(source, category, field)


def record(source: OAuthSource, category: str, *, blocked: bool = False) -> None:
    """Increment the current UTC-hour bucket. Never raises."""
    field = "blocked_count" if blocked else "count"
    try:
        _increment_bucket(source, category, field)
    except Exception as exc:  # noqa: BLE001 - recording must never break a DingTalk call
        LOGGER.warning(
            "dingtalk_usage_record_failed",
            source_pk=str(source.pk),
            source_slug=getattr(source, "slug", None),
            category=category,
            field=field,
            exception_type=type(exc).__name__,
        )


def check(source: OAuthSource, category: str) -> None:
    """Refuse a call according to the cached policy, or allow it.

    ``ak_token`` is never refused. A missing or expired policy allows everything.
    Policy-check infrastructure failures fail open. A refusal increments
    ``blocked_count`` and raises :class:`DingTalkUsagePolicyBlocked`.
    """
    try:
        refused = _should_refuse(source, category)
    except Exception as exc:  # noqa: BLE001 - policy infrastructure failures fail open
        LOGGER.warning(
            "dingtalk_usage_policy_check_failed",
            source_pk=str(source.pk),
            source_slug=getattr(source, "slug", None),
            category=category,
            exception_type=type(exc).__name__,
        )
        return
    if not refused:
        return
    record(source, category, blocked=True)
    raise DingTalkUsagePolicyBlocked(category)


def prepare_outbound_call(source: OAuthSource, category: str) -> None:
    """Apply usage policy then record one outbound DingTalk HTTP attempt."""
    check(source, category)
    record(source, category, blocked=False)


def purge_expired_usage_buckets() -> int:
    """Delete usage buckets older than :data:`USAGE_RETENTION_DAYS`."""
    cutoff = datetime.now(UTC) - timedelta(days=USAGE_RETENTION_DAYS)
    deleted, _aliases = DingTalkApiUsageBucket.objects.filter(hour_start__lt=cutoff).delete()
    return deleted
