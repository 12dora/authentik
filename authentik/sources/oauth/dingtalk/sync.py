"""DingTalk directory sync orchestration."""

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils.timezone import now
from requests.exceptions import RequestException
from structlog.stdlib import get_logger

from authentik.sources.oauth.dingtalk.client import DingTalkDirectoryClient
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)

LOGGER = get_logger()


def normalize_id_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple | set):
        return []
    return sorted({str(item) for item in value if item is not None})


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
        "dept_id_list": normalize_id_list(raw.get("dept_id_list") or raw.get("deptIdList")),
        "active": bool(raw.get("active", True)),
        "raw": raw,
    }


def _publish_snapshot(
    source: OAuthSource,
    corp_id: str,
    departments: list[dict[str, Any]],
    users_by_id: dict[str, dict[str, Any]],
    started_at: datetime,
    warnings: list[str],
) -> dict[str, Any]:
    seen_depts: set[str] = set()
    seen_users: set[str] = set()
    with transaction.atomic():
        status = DingTalkDirectorySyncStatus.objects.select_for_update().get(
            source=source, corp_id=corp_id
        )
        for department in departments:
            seen_depts.add(department["dept_id"])
            DingTalkDirectoryDepartment.objects.update_or_create(
                source=source,
                corp_id=corp_id,
                dept_id=department["dept_id"],
                defaults={
                    "name": department["name"],
                    "parent_dept_id": department["parent_dept_id"],
                    "raw": department["raw"],
                    "is_deleted": False,
                    "last_seen_at": started_at,
                },
            )
        for user in users_by_id.values():
            seen_users.add(user["user_id"])
            DingTalkDirectoryUser.objects.update_or_create(
                source=source,
                corp_id=corp_id,
                user_id=user["user_id"],
                defaults={**user, "is_deleted": False, "last_seen_at": started_at},
            )

        DingTalkDirectoryDepartment.objects.filter(source=source, corp_id=corp_id).exclude(
            dept_id__in=seen_depts
        ).update(is_deleted=True)
        DingTalkDirectoryUser.objects.filter(source=source, corp_id=corp_id).exclude(
            user_id__in=seen_users
        ).update(is_deleted=True)
        counters = {
            "departments": DingTalkDirectoryDepartment.objects.filter(
                source=source, corp_id=corp_id, is_deleted=False
            ).count(),
            "users": DingTalkDirectoryUser.objects.filter(
                source=source, corp_id=corp_id, is_deleted=False
            ).count(),
            "warnings": warnings,
        }
        status.status = "success"
        status.generation += 1
        status.error = ""
        status.counters = counters
        status.finished_at = now()
        status.save()
    return counters


def sync_dingtalk_directory(source: OAuthSource, corp_id: str) -> dict[str, Any]:
    """Sync departments and users for one DingTalk source/corp pair."""
    if source.provider_type != "dingtalk":
        raise ValueError("Source is not a DingTalk OAuth source.")
    started_at = now()
    DingTalkDirectorySyncStatus.objects.update_or_create(
        source=source,
        corp_id=str(corp_id),
        defaults={
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "error": "",
        },
    )
    client = DingTalkDirectoryClient(source)
    counters = {"departments": 0, "users": 0}
    try:
        departments = [{"dept_id": "1", "name": "", "parent_dept_id": "", "raw": {"dept_id": "1"}}]
        departments.extend(list(client.iter_departments()))
        users_by_id: dict[str, dict[str, Any]] = {}
        for department in departments:
            for raw_user in client.iter_department_users(department["dept_id"]):
                user = normalize_dingtalk_user(raw_user, str(corp_id))
                users_by_id[user["user_id"]] = user

        # C4: topapi/v2/user/list never returns manager_userid at all (verified 2026-07-06:
        # its rows only carry the dept-leader boolean). The direct-manager field is exposed
        # exclusively by the per-user detail endpoint, so enrich every synced user with one
        # topapi/v2/user/get call; failures degrade to an empty manager for that user only.
        for user_id, user in users_by_id.items():
            try:
                detail = client.get_user_detail(user_id)
            except ValueError, RequestException:
                continue
            manager_id = detail.get("manager_userid") or detail.get("managerUserId") or ""
            if manager_id:
                user["manager_user_id"] = str(manager_id)

        # An all-empty result after enrichment almost always means the org never maintained
        # the direct-manager field in the DingTalk admin backend (contacts editor / smart HR
        # roster) — there is no separately grantable permission point for it. unionid is
        # required for downstream user resolution. Surface warnings when these are broadly
        # missing so the managed-user hierarchy does not silently break.
        total_users = len(users_by_id)
        warnings: list[str] = []
        if total_users > 1 and all(not user["manager_user_id"] for user in users_by_id.values()):
            warnings.append(
                "No DingTalk user reported a manager_userid; the org has probably never "
                "maintained the direct-manager field in the DingTalk admin backend "
                "(contacts editor / smart HR roster), so managed-user hierarchies will "
                "be empty until it is filled in there."
            )
        missing_union = sum(1 for user in users_by_id.values() if not user["union_id"])
        if missing_union:
            warnings.append(
                f"{missing_union}/{total_users} DingTalk users have no unionId; downstream "
                "user resolution may be incomplete for them."
            )
        if warnings:
            LOGGER.warning(
                "dingtalk_directory_sync_warnings",
                source_slug=source.slug,
                corp_id=str(corp_id),
                warnings=warnings,
            )

        return _publish_snapshot(
            source, str(corp_id), departments, users_by_id, started_at, warnings
        )
    except Exception as exc:
        DingTalkDirectorySyncStatus.objects.update_or_create(
            source=source,
            corp_id=str(corp_id),
            defaults={
                "status": "error",
                "error": str(exc),
                "counters": counters,
                "finished_at": now(),
            },
        )
        raise
