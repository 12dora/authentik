"""DingTalk directory sync orchestration."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from json import dumps
from typing import Any
from uuid import UUID, uuid4

from django.core.cache import cache
from django.db import connection, transaction
from django.utils.timezone import now
from requests.exceptions import RequestException
from structlog.stdlib import get_logger

from authentik.sources.oauth.dingtalk.client import DingTalkDirectoryClient
from authentik.sources.oauth.dingtalk.config import normalize_dingtalk_id_list
from authentik.sources.oauth.dingtalk.redaction import redact_dingtalk_detail
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectoryDepartmentStage,
    DingTalkDirectorySyncStatus,
    DingTalkDirectorySyncStatusChoices,
    DingTalkDirectoryUser,
    DingTalkDirectoryUserStage,
    OAuthSource,
)
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_CORP_ID_ECHO_KEYS,
    DingTalkAppTokenError,
    DingTalkDepartmentCorpUnavailable,
    DingTalkDepartmentLoadFailed,
    extract_dingtalk_corp_ids,
    fetch_dingtalk_org_auth_info,
)

LOGGER = get_logger()

DINGTALK_MAX_SYNC_USERS = 100000
DINGTALK_MAX_RAW_PAYLOAD_BYTES = 50 * 1024 * 1024
DINGTALK_STAGE_BATCH_SIZE = 500
DINGTALK_MAX_CONCURRENT_SYNCS = 4
DINGTALK_CONCURRENCY_TIMEOUT = 15 * 60
DINGTALK_SYNC_ERROR_APP_TOKEN_FAILED = "dingtalk_directory_app_token_failed"
DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE = "dingtalk_directory_broker_unavailable"
DINGTALK_SYNC_ERROR_CONCURRENCY_LIMIT = "dingtalk_directory_concurrency_limit"
DINGTALK_SYNC_ERROR_CORP_MISMATCH = "dingtalk_directory_corp_mismatch"
DINGTALK_SYNC_ERROR_CORP_UNAUTHORIZED = "dingtalk_directory_corp_unauthorized"
DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED = "dingtalk_directory_http_request_failed"
DINGTALK_SYNC_ERROR_INVALID_RESPONSE = "dingtalk_directory_invalid_response"
DINGTALK_SYNC_ERROR_PAYLOAD_LIMIT = "dingtalk_directory_payload_limit"
DINGTALK_SYNC_ERROR_RUN_STALE = "dingtalk_directory_run_stale"
DINGTALK_SYNC_ERROR_SOURCE_DISABLED = "dingtalk_directory_source_disabled"
DINGTALK_SYNC_ERROR_SOURCE_UNAVAILABLE = "dingtalk_directory_source_unavailable"
DINGTALK_SYNC_ERROR_UNSUPPORTED_SOURCE = "dingtalk_directory_unsupported_source"
DINGTALK_SYNC_ERROR_USER_LIMIT = "dingtalk_directory_user_limit"
DINGTALK_SYNC_ERROR_USER_DETAIL_FAILED = "dingtalk_directory_user_detail_failed"
DINGTALK_SYNC_ERROR_UNKNOWN = "dingtalk_directory_sync_failed"
DINGTALK_SYNC_ERROR_CODES = frozenset(
    {
        DINGTALK_SYNC_ERROR_APP_TOKEN_FAILED,
        DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE,
        DINGTALK_SYNC_ERROR_CONCURRENCY_LIMIT,
        DINGTALK_SYNC_ERROR_CORP_MISMATCH,
        DINGTALK_SYNC_ERROR_CORP_UNAUTHORIZED,
        DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED,
        DINGTALK_SYNC_ERROR_INVALID_RESPONSE,
        DINGTALK_SYNC_ERROR_PAYLOAD_LIMIT,
        DINGTALK_SYNC_ERROR_RUN_STALE,
        DINGTALK_SYNC_ERROR_SOURCE_DISABLED,
        DINGTALK_SYNC_ERROR_SOURCE_UNAVAILABLE,
        DINGTALK_SYNC_ERROR_UNSUPPORTED_SOURCE,
        DINGTALK_SYNC_ERROR_USER_LIMIT,
        DINGTALK_SYNC_ERROR_USER_DETAIL_FAILED,
        DINGTALK_SYNC_ERROR_UNKNOWN,
    }
)
DINGTALK_SYNC_ERROR_MAX_PARAMS = 10


def _typed_counters(
    *, departments: int = 0, users: int = 0, warnings: list[str] | None = None
) -> dict[str, Any]:
    return {
        "departments": int(departments),
        "users": int(users),
        "warnings": list(warnings or []),
    }


@contextmanager
def _sync_concurrency_lease():
    key = "authentik/sources/oauth/dingtalk/directory/concurrency"
    if cache.get(key) is None:
        cache.set(key, 0, DINGTALK_CONCURRENCY_TIMEOUT)
    try:
        active = cache.incr(key)
    except ValueError:
        cache.set(key, 1, DINGTALK_CONCURRENCY_TIMEOUT)
        active = 1
    if active > DINGTALK_MAX_CONCURRENT_SYNCS:
        cache.decr(key)
        raise ValueError("DingTalk directory sync concurrency budget exceeded.")
    try:
        yield
    finally:
        try:
            cache.decr(key)
        except ValueError:
            cache.delete(key)


def _redacted_error_detail(exc: Exception) -> str:
    return redact_dingtalk_detail(exc) or "DingTalk directory sync failed."


def _bounded_error_params(params: dict[str, Any] | None) -> dict[str, Any]:
    bounded: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if len(bounded) >= DINGTALK_SYNC_ERROR_MAX_PARAMS:
            break
        bounded[str(key)[:64]] = str(value)[:128]
    return bounded


def _http_error_params(exc: BaseException) -> dict[str, Any]:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    return {"status_code": status_code} if status_code is not None else {}


# Failures that carry an operator-facing (and, for the corp one, translated) message.
# Match them by type: substring matching would misclassify them under a non-English
# locale, and would lump a bad app secret in with "DingTalk sent something unparseable".
_SYNC_ERROR_BY_TYPE: tuple[tuple[type[Exception], str], ...] = (
    (DingTalkDepartmentCorpUnavailable, DINGTALK_SYNC_ERROR_CORP_UNAUTHORIZED),
    (DingTalkAppTokenError, DINGTALK_SYNC_ERROR_APP_TOKEN_FAILED),
)

# Failures raised as plain ValueErrors inside this package, matched on their own wording.
_SYNC_ERROR_BY_MESSAGE: tuple[tuple[tuple[str, ...], str, dict[str, Any]], ...] = (
    (
        ("did not report a corp identity",),
        DINGTALK_SYNC_ERROR_CORP_MISMATCH,
        {"reason": "unverified"},
    ),
    (
        ("reported a different corp identity",),
        DINGTALK_SYNC_ERROR_CORP_MISMATCH,
        {"reason": "mismatch"},
    ),
    (("concurrency budget",), DINGTALK_SYNC_ERROR_CONCURRENCY_LIMIT, {}),
    (("user limit",), DINGTALK_SYNC_ERROR_USER_LIMIT, {}),
    (("payload limit",), DINGTALK_SYNC_ERROR_PAYLOAD_LIMIT, {}),
    (("user detail failed",), DINGTALK_SYNC_ERROR_USER_DETAIL_FAILED, {}),
    (
        ("no longer current", "deleted before it started"),
        DINGTALK_SYNC_ERROR_RUN_STALE,
        {},
    ),
    (("not a DingTalk OAuth source",), DINGTALK_SYNC_ERROR_UNSUPPORTED_SOURCE, {}),
    (("source is disabled",), DINGTALK_SYNC_ERROR_SOURCE_DISABLED, {}),
)


def classify_dingtalk_sync_error(exc: Exception) -> tuple[str, dict[str, Any]]:
    """Return stable public error metadata for a DingTalk sync failure."""
    if isinstance(exc, RequestException):
        return DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED, _http_error_params(exc)
    for exc_type, error_code in _SYNC_ERROR_BY_TYPE:
        if isinstance(exc, exc_type):
            return error_code, {}
    # The org lookup wraps its cause, so read through it rather than reporting a
    # transport failure as an unparseable response.
    if isinstance(exc, DingTalkDepartmentLoadFailed) and isinstance(
        exc.__cause__, RequestException
    ):
        return DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED, _http_error_params(exc.__cause__)
    if not isinstance(exc, ValueError):
        return DINGTALK_SYNC_ERROR_UNKNOWN, {"exception_type": type(exc).__name__}
    message = str(exc)
    for fragments, error_code, params in _SYNC_ERROR_BY_MESSAGE:
        if any(fragment in message for fragment in fragments):
            return error_code, dict(params)
    return DINGTALK_SYNC_ERROR_INVALID_RESPONSE, {}


def safe_dingtalk_sync_error(exc: Exception) -> str:
    """Return the stable public error code for a DingTalk sync failure."""
    error_code, _params = classify_dingtalk_sync_error(exc)
    return error_code


def finalize_dingtalk_directory_sync_error(
    *,
    source: OAuthSource | None = None,
    source_pk: str | None = None,
    corp_id: str,
    run_id: str | UUID | None,
    exc: Exception | None = None,
    error_code: str | None = None,
    error_params: dict[str, Any] | None = None,
) -> bool:
    """Mark the matching active DingTalk sync run as terminal ERROR."""
    if not run_id:
        return False
    parsed_run_id = UUID(str(run_id))
    if exc and not error_code:
        error_code, classified_params = classify_dingtalk_sync_error(exc)
        error_params = {**classified_params, **(error_params or {})}
    error_code = error_code or DINGTALK_SYNC_ERROR_UNKNOWN
    if error_code not in DINGTALK_SYNC_ERROR_CODES:
        error_code = DINGTALK_SYNC_ERROR_UNKNOWN
        error_params = {"legacy_error": "redacted", **(error_params or {})}
    error_params = _bounded_error_params(error_params)
    correlation_id = uuid4()
    lookup = {"corp_id": str(corp_id), "active_run_id": parsed_run_id}
    if source is not None:
        lookup["source"] = source
    elif source_pk is not None:
        lookup["source_id"] = source_pk
    else:
        return False
    with transaction.atomic():
        status = DingTalkDirectorySyncStatus.objects.select_for_update().filter(**lookup).first()
        if not status:
            return False
        status.status = DingTalkDirectorySyncStatusChoices.ERROR
        status.error = error_code
        status.error_code = error_code
        status.error_params = error_params
        status.error_correlation_id = correlation_id
        status.finished_at = now()
        status.active_run_id = None
        status.save()
    LOGGER.warning(
        "dingtalk_directory_sync_failed",
        source_pk=str(source.pk) if source is not None else str(source_pk),
        source_slug=source.slug if source is not None else None,
        corp_id=str(corp_id),
        run_id=str(parsed_run_id),
        error_code=error_code,
        error_params=error_params,
        error_correlation_id=str(correlation_id),
        exception_type=type(exc).__name__ if exc else None,
        error_detail=_redacted_error_detail(exc) if exc else "",
    )
    return True


def _start_sync_run(source: OAuthSource, corp_id: str, started_at: datetime) -> tuple[UUID, int]:
    run_id = uuid4()
    with transaction.atomic():
        DingTalkDirectorySyncStatus.objects.get_or_create(source=source, corp_id=corp_id)
        status = DingTalkDirectorySyncStatus.objects.select_for_update().get(
            source=source, corp_id=corp_id
        )
        status.run_sequence += 1
        status.active_run_id = run_id
        status.status = DingTalkDirectorySyncStatusChoices.RUNNING
        status.started_at = started_at
        status.last_attempt_at = started_at
        status.finished_at = None
        status.error = ""
        status.error_code = ""
        status.error_params = {}
        status.error_correlation_id = None
        status.save()
        return run_id, status.run_sequence


def queue_dingtalk_directory_sync(
    source: OAuthSource,
    corp_id: str,
    queued_at: datetime | None = None,
) -> tuple[UUID, bool]:
    """Create a durable queued run for a source/corp pair.

    Returns the active run id and whether the caller should enqueue worker execution.
    """
    queued_at = queued_at or now()
    with transaction.atomic():
        DingTalkDirectorySyncStatus.objects.get_or_create(source=source, corp_id=corp_id)
        status = DingTalkDirectorySyncStatus.objects.select_for_update().get(
            source=source, corp_id=corp_id
        )
        if status.status in {
            DingTalkDirectorySyncStatusChoices.QUEUED,
            DingTalkDirectorySyncStatusChoices.RUNNING,
        } and status.active_run_id:
            return status.active_run_id, False
        status.run_sequence += 1
        status.active_run_id = uuid4()
        status.status = DingTalkDirectorySyncStatusChoices.QUEUED
        status.started_at = None
        status.last_attempt_at = queued_at
        status.finished_at = None
        status.error = ""
        status.error_code = ""
        status.error_params = {}
        status.error_correlation_id = None
        status.counters = _typed_counters()
        status.save()
        return status.active_run_id, True


def _claim_sync_run(
    source: OAuthSource,
    corp_id: str,
    started_at: datetime,
    queued_run_id: UUID | None,
) -> tuple[UUID, int]:
    if queued_run_id is None:
        return _start_sync_run(source, corp_id, started_at)
    with transaction.atomic():
        status = DingTalkDirectorySyncStatus.objects.select_for_update().get(
            source=source, corp_id=corp_id
        )
        if status.status == DingTalkDirectorySyncStatusChoices.DELETED:
            raise ValueError("DingTalk directory sync was deleted before it started.")
        if status.active_run_id != queued_run_id:
            raise ValueError("DingTalk directory sync run is no longer current.")
        status.status = DingTalkDirectorySyncStatusChoices.RUNNING
        status.started_at = started_at
        status.last_attempt_at = started_at
        status.finished_at = None
        status.error = ""
        status.error_code = ""
        status.error_params = {}
        status.error_correlation_id = None
        status.save()
        return queued_run_id, status.run_sequence


def _normalize_dingtalk_bool(value: Any, field_name: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"DingTalk user field {field_name} was not a boolean.")


def normalize_dingtalk_user(raw: dict[str, Any], corp_id: str) -> dict[str, Any]:
    user_id = raw.get("userid") or raw.get("userId") or raw.get("user_id")
    if not user_id:
        raise ValueError("DingTalk user did not include userid.")
    return {
        "corp_id": str(corp_id),
        "user_id": str(user_id),
        "union_id": raw.get("unionid") or raw.get("unionId") or "",
        "open_id": raw.get("openId") or raw.get("open_id") or "",
        "name": raw.get("name") or raw.get("nick") or "",
        "avatar": raw.get("avatar") or raw.get("avatarUrl") or "",
        "title": raw.get("title") or "",
        "email": raw.get("email") or "",
        "mobile": raw.get("mobile") or "",
        "job_number": raw.get("job_number") or raw.get("jobNumber") or "",
        "manager_user_id": raw.get("manager_userid") or raw.get("managerUserId") or "",
        "dept_id_list": normalize_dingtalk_id_list(
            raw.get("dept_id_list") or raw.get("deptIdList")
        ),
        "active": _normalize_dingtalk_bool(raw.get("active"), "active", True),
        "raw": raw,
    }


def _verify_sync_corp(source: OAuthSource, corp_id: str, client: DingTalkDirectoryClient) -> None:
    """Confirm the app credentials really speak for the corp this run writes into.

    The directory endpoints used below are keyed only by the app token — none of them
    takes a corp_id — so a token bound to corp B would happily fill corp A's cache rows.
    This check is the only thing standing between that and the per-tenant isolation the
    directory contract promises, so an unverifiable identity fails the run.
    """
    org_info = fetch_dingtalk_org_auth_info(source, corp_id, session=client.session)
    raw = org_info.get("raw") if isinstance(org_info.get("raw"), dict) else {}
    verified_corp_ids = extract_dingtalk_corp_ids(raw)
    if not verified_corp_ids:
        # Some app types only echo back the corp the request asked about. DingTalk
        # answering at all for that targetCorpId already means the app is authorized for
        # it, so accept the echo rather than failing over a response shape — but only
        # once the response has stated no corp identity of its own to check against.
        verified_corp_ids = extract_dingtalk_corp_ids(raw, keys=DINGTALK_CORP_ID_ECHO_KEYS)
    if not verified_corp_ids:
        raise ValueError("DingTalk organization verification did not report a corp identity.")
    if corp_id not in verified_corp_ids:
        raise ValueError("DingTalk organization verification reported a different corp identity.")


def _iter_departments(client: DingTalkDirectoryClient) -> Iterator[dict[str, Any]]:
    yield {"dept_id": "1", "name": "", "parent_dept_id": "", "raw": {"dept_id": "1"}}
    yield from client.iter_departments()


def _raw_size(raw: dict[str, Any]) -> int:
    return len(dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _check_stage_budgets(user_count: int, raw_payload_bytes: int) -> None:
    if user_count > DINGTALK_MAX_SYNC_USERS:
        raise ValueError("DingTalk directory sync user limit exceeded.")
    if raw_payload_bytes > DINGTALK_MAX_RAW_PAYLOAD_BYTES:
        raise ValueError("DingTalk directory sync payload limit exceeded.")


def _flush_department_stage(
    source: OAuthSource,
    corp_id: str,
    run_id: UUID,
    departments: list[dict[str, Any]],
) -> None:
    if not departments:
        return
    DingTalkDirectoryDepartmentStage.objects.bulk_create(
        [
            DingTalkDirectoryDepartmentStage(
                source=source,
                corp_id=corp_id,
                run_id=run_id,
                dept_id=department["dept_id"],
                name=department["name"],
                parent_dept_id=department["parent_dept_id"],
                raw=department["raw"],
            )
            for department in departments
        ],
        batch_size=DINGTALK_STAGE_BATCH_SIZE,
        update_conflicts=True,
        update_fields=["name", "parent_dept_id", "raw"],
        unique_fields=["source", "corp_id", "run_id", "dept_id"],
    )
    departments.clear()


def _flush_user_stage(
    source: OAuthSource,
    corp_id: str,
    run_id: UUID,
    users: list[dict[str, Any]],
) -> None:
    if not users:
        return
    DingTalkDirectoryUserStage.objects.bulk_create(
        [
            DingTalkDirectoryUserStage(
                source=source,
                corp_id=corp_id,
                run_id=run_id,
                user_id=user["user_id"],
                union_id=user["union_id"],
                open_id=user["open_id"],
                name=user["name"],
                avatar=user["avatar"],
                title=user["title"],
                email=user["email"],
                mobile=user["mobile"],
                job_number=user["job_number"],
                manager_user_id=user["manager_user_id"],
                dept_id_list=user["dept_id_list"],
                active=user["active"],
                raw=user["raw"],
            )
            for user in users
        ],
        batch_size=DINGTALK_STAGE_BATCH_SIZE,
        update_conflicts=True,
        update_fields=[
            "union_id",
            "open_id",
            "name",
            "avatar",
            "title",
            "email",
            "mobile",
            "job_number",
            "manager_user_id",
            "dept_id_list",
            "active",
            "raw",
        ],
        unique_fields=["source", "corp_id", "run_id", "user_id"],
    )
    users.clear()


def _record_stage_checkpoint(
    source: OAuthSource,
    corp_id: str,
    run_id: UUID,
    department_count: int,
    user_count: int,
    current_department_id: str,
) -> None:
    DingTalkDirectorySyncStatus.objects.filter(
        source=source,
        corp_id=corp_id,
        active_run_id=run_id,
    ).update(
        counters={
            "departments_staged": department_count,
            "users_staged": user_count,
            "checkpoint": {"dept_id": current_department_id},
        }
    )


def _stage_user(
    client: DingTalkDirectoryClient,
    corp_id: str,
    raw_user: dict[str, Any],
) -> dict[str, Any]:
    user = normalize_dingtalk_user(raw_user, corp_id)
    try:
        detail = client.get_user_detail(user["user_id"])
    except (ValueError, RequestException) as exc:
        raise ValueError("DingTalk user detail failed; snapshot was not published.") from exc
    manager_id = detail.get("manager_userid") or detail.get("managerUserId") or ""
    if manager_id:
        user["manager_user_id"] = str(manager_id)
    return user


def _stage_directory_snapshot(
    source: OAuthSource,
    corp_id: str,
    run_id: UUID,
    client: DingTalkDirectoryClient,
) -> None:
    DingTalkDirectoryDepartmentStage.objects.filter(
        source=source, corp_id=corp_id, run_id=run_id
    ).delete()
    DingTalkDirectoryUserStage.objects.filter(
        source=source, corp_id=corp_id, run_id=run_id
    ).delete()
    department_batch: list[dict[str, Any]] = []
    user_batch: list[dict[str, Any]] = []
    department_count = 0
    user_count = 0
    raw_payload_bytes = 0
    for department in _iter_departments(client):
        department_batch.append(department)
        department_count += 1
        raw_payload_bytes += _raw_size(department["raw"])
        _check_stage_budgets(user_count, raw_payload_bytes)
        if len(department_batch) >= DINGTALK_STAGE_BATCH_SIZE:
            _flush_department_stage(source, corp_id, run_id, department_batch)
            _record_stage_checkpoint(
                source, corp_id, run_id, department_count, user_count, department["dept_id"]
            )
        for raw_user in client.iter_department_users(department["dept_id"]):
            user = _stage_user(client, corp_id, raw_user)
            user_batch.append(user)
            user_count += 1
            raw_payload_bytes += _raw_size(raw_user)
            _check_stage_budgets(user_count, raw_payload_bytes)
            if len(user_batch) >= DINGTALK_STAGE_BATCH_SIZE:
                _flush_user_stage(source, corp_id, run_id, user_batch)
                _record_stage_checkpoint(
                    source, corp_id, run_id, department_count, user_count, department["dept_id"]
                )
    _flush_department_stage(source, corp_id, run_id, department_batch)
    _flush_user_stage(source, corp_id, run_id, user_batch)
    _record_stage_checkpoint(source, corp_id, run_id, department_count, user_count, "")


def _snapshot_warnings(
    source: OAuthSource,
    corp_id: str,
    run_id: UUID,
) -> list[str]:
    users = DingTalkDirectoryUserStage.objects.filter(source=source, corp_id=corp_id, run_id=run_id)
    total_users = users.count()
    warnings: list[str] = []
    if total_users > 1 and users.filter(manager_user_id="").count() == total_users:
        warnings.append(
            "No DingTalk user reported a manager_userid; the org has probably never "
            "maintained the direct-manager field in the DingTalk admin backend "
            "(contacts editor / smart HR roster), so managed-user hierarchies will "
            "be empty until it is filled in there."
        )
    missing_union = users.filter(union_id="").count()
    if missing_union:
        warnings.append(
            f"{missing_union}/{total_users} DingTalk users have no unionId; downstream "
            "user resolution may be incomplete for them."
        )
    return warnings


def _cleanup_staging(source: OAuthSource, corp_id: str, run_id: UUID) -> None:
    DingTalkDirectoryDepartmentStage.objects.filter(
        source=source, corp_id=corp_id, run_id=run_id
    ).delete()
    DingTalkDirectoryUserStage.objects.filter(
        source=source, corp_id=corp_id, run_id=run_id
    ).delete()


def _publish_snapshot(
    source: OAuthSource,
    corp_id: str,
    started_at: datetime,
    warnings: list[str],
    run: tuple[UUID, int],
) -> dict[str, Any]:
    run_id, run_sequence = run
    with transaction.atomic():
        status = DingTalkDirectorySyncStatus.objects.select_for_update().get(
            source=source, corp_id=corp_id
        )
        if status.active_run_id != run_id or status.run_sequence != run_sequence:
            return {"departments": 0, "users": 0, "warnings": [], "stale": True}
        _bulk_finalize_departments(source, corp_id, run_id, started_at)
        _bulk_finalize_users(source, corp_id, run_id, started_at)
        _soft_delete_missing_from_staging(source, corp_id, run_id)
        counters = _typed_counters(
            departments=DingTalkDirectoryDepartment.objects.filter(
                source=source, corp_id=corp_id, is_deleted=False
            ).count(),
            users=DingTalkDirectoryUser.objects.filter(
                source=source, corp_id=corp_id, is_deleted=False
            ).count(),
            warnings=warnings,
        )
        status.status = DingTalkDirectorySyncStatusChoices.SUCCESS
        status.generation = run_sequence
        status.active_run_id = None
        status.error = ""
        status.error_code = ""
        status.error_params = {}
        status.error_correlation_id = None
        status.counters = counters
        status.finished_at = now()
        status.last_success_at = status.finished_at
        status.save()
    return counters


def _bulk_finalize_departments(
    source: OAuthSource,
    corp_id: str,
    run_id: UUID,
    seen_at: datetime,
) -> None:
    cache_table = connection.ops.quote_name(DingTalkDirectoryDepartment._meta.db_table)
    stage_table = connection.ops.quote_name(DingTalkDirectoryDepartmentStage._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {cache_table}
                (source_id, corp_id, dept_id, name, parent_dept_id, raw,
                 is_deleted, last_seen_at, last_updated)
            SELECT source_id, corp_id, dept_id, name, parent_dept_id, raw,
                   false, %s, %s
            FROM {stage_table}
            WHERE source_id = %s AND corp_id = %s AND run_id = %s
            ON CONFLICT (source_id, corp_id, dept_id) DO UPDATE SET
                name = EXCLUDED.name,
                parent_dept_id = EXCLUDED.parent_dept_id,
                raw = EXCLUDED.raw,
                is_deleted = false,
                last_seen_at = EXCLUDED.last_seen_at,
                last_updated = EXCLUDED.last_updated
            """,
            [seen_at, now(), source.pk, corp_id, run_id],
        )


def _bulk_finalize_users(
    source: OAuthSource,
    corp_id: str,
    run_id: UUID,
    seen_at: datetime,
) -> None:
    cache_table = connection.ops.quote_name(DingTalkDirectoryUser._meta.db_table)
    stage_table = connection.ops.quote_name(DingTalkDirectoryUserStage._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {cache_table}
                (source_id, corp_id, user_id, union_id, open_id, name, avatar, title,
                 email, mobile, job_number, manager_user_id, dept_id_list, active, raw,
                 is_deleted, last_seen_at, last_updated)
            SELECT source_id, corp_id, user_id, union_id, open_id, name, avatar, title,
                   email, mobile, job_number, manager_user_id, dept_id_list, active, raw,
                   false, %s, %s
            FROM {stage_table}
            WHERE source_id = %s AND corp_id = %s AND run_id = %s
            ON CONFLICT (source_id, corp_id, user_id) DO UPDATE SET
                union_id = EXCLUDED.union_id,
                open_id = EXCLUDED.open_id,
                name = EXCLUDED.name,
                avatar = EXCLUDED.avatar,
                title = EXCLUDED.title,
                email = EXCLUDED.email,
                mobile = EXCLUDED.mobile,
                job_number = EXCLUDED.job_number,
                manager_user_id = EXCLUDED.manager_user_id,
                dept_id_list = EXCLUDED.dept_id_list,
                active = EXCLUDED.active,
                raw = EXCLUDED.raw,
                is_deleted = false,
                last_seen_at = EXCLUDED.last_seen_at,
                last_updated = EXCLUDED.last_updated
            """,
            [seen_at, now(), source.pk, corp_id, run_id],
        )


def _soft_delete_missing_from_staging(source: OAuthSource, corp_id: str, run_id: UUID) -> None:
    department_table = connection.ops.quote_name(DingTalkDirectoryDepartment._meta.db_table)
    department_stage = connection.ops.quote_name(DingTalkDirectoryDepartmentStage._meta.db_table)
    user_table = connection.ops.quote_name(DingTalkDirectoryUser._meta.db_table)
    user_stage = connection.ops.quote_name(DingTalkDirectoryUserStage._meta.db_table)
    updated_at = now()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {department_table} cache
            SET is_deleted = true, last_updated = %s
            WHERE cache.source_id = %s AND cache.corp_id = %s
              AND NOT EXISTS (
                  SELECT 1 FROM {department_stage} stage
                  WHERE stage.source_id = cache.source_id
                    AND stage.corp_id = cache.corp_id
                    AND stage.run_id = %s
                    AND stage.dept_id = cache.dept_id
              )
            """,
            [updated_at, source.pk, corp_id, run_id],
        )
        cursor.execute(
            f"""
            UPDATE {user_table} cache
            SET is_deleted = true, last_updated = %s
            WHERE cache.source_id = %s AND cache.corp_id = %s
              AND NOT EXISTS (
                  SELECT 1 FROM {user_stage} stage
                  WHERE stage.source_id = cache.source_id
                    AND stage.corp_id = cache.corp_id
                    AND stage.run_id = %s
                    AND stage.user_id = cache.user_id
              )
            """,
            [updated_at, source.pk, corp_id, run_id],
        )


def sync_dingtalk_directory(
    source: OAuthSource, corp_id: str, queued_run_id: str | UUID | None = None
) -> dict[str, Any]:
    """Sync departments and users for one DingTalk source/corp pair."""
    if source.provider_type != "dingtalk":
        raise ValueError("Source is not a DingTalk OAuth source.")
    if not source.enabled:
        raise ValueError("DingTalk source is disabled.")
    started_at = now()
    corp_id = str(corp_id)
    parsed_run_id = UUID(str(queued_run_id)) if queued_run_id else None
    run_id, run_sequence = _claim_sync_run(source, corp_id, started_at, parsed_run_id)
    client = DingTalkDirectoryClient(source)
    try:
        with _sync_concurrency_lease():
            _verify_sync_corp(source, corp_id, client)
            _stage_directory_snapshot(source, corp_id, run_id, client)

            # An all-empty result after enrichment almost always means the org never maintained
            # the direct-manager field in the DingTalk admin backend (contacts editor / smart HR
            # roster) — there is no separately grantable permission point for it. unionid is
            # required for downstream user resolution. Surface warnings when these are broadly
            # missing so the managed-user hierarchy does not silently break.
            warnings = _snapshot_warnings(source, corp_id, run_id)
            if warnings:
                LOGGER.warning(
                    "dingtalk_directory_sync_warnings",
                    source_slug=source.slug,
                    corp_id=str(corp_id),
                    warnings=warnings,
                )

            result = _publish_snapshot(
                source,
                corp_id,
                started_at,
                warnings,
                (run_id, run_sequence),
            )
            _cleanup_staging(source, corp_id, run_id)
            return result
    except Exception as exc:
        finalize_dingtalk_directory_sync_error(
            source=source,
            corp_id=corp_id,
            run_id=run_id,
            exc=exc,
            error_params={"run_sequence": run_sequence},
        )
        raise
