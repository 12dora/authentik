"""DingTalk directory sync orchestration."""

from typing import Any

from django.db import transaction
from django.utils.timezone import now
from structlog.stdlib import get_logger

from authentik.sources.oauth.dingtalk.client import DingTalkDirectoryClient
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)

LOGGER = get_logger()


def _should_skip_orphan_deletion(seen_count: int, previous_count: int) -> bool:
    """Return True when soft-deleting orphans would be unsafe for this sync result.

    A sync that returns zero records for a corp that previously had records is almost certainly
    incomplete (throttling / transient permission loss / an empty page), so it must not wipe the
    whole cache — the next full sync self-heals (C2). A partial-but-non-empty fetch still applies
    deletions so genuine departures are reflected.
    """
    return previous_count > 0 and seen_count == 0


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
    seen_depts: set[str] = set()
    seen_users: set[str] = set()
    try:
        departments = [
            {"dept_id": "1", "name": "", "parent_dept_id": "", "raw": {"dept_id": "1"}}
        ]
        departments.extend(list(client.iter_departments()))
        users_by_id: dict[str, dict[str, Any]] = {}
        for department in departments:
            for raw_user in client.iter_department_users(department["dept_id"]):
                user = normalize_dingtalk_user(raw_user, str(corp_id))
                users_by_id[user["user_id"]] = user

        # C4: user/list only returns manager_userid when the app has the direct-manager
        # permission, and unionid is required for downstream user resolution. Surface warnings
        # when these are broadly missing so the managed-user hierarchy does not silently break.
        total_users = len(users_by_id)
        warnings: list[str] = []
        if total_users > 1 and all(not user["manager_user_id"] for user in users_by_id.values()):
            warnings.append(
                "No DingTalk user reported a manager_userid; the app likely lacks the "
                "'read employee direct manager' permission, so managed-user hierarchies "
                "will be empty."
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

        with transaction.atomic():
            status = DingTalkDirectorySyncStatus.objects.select_for_update().get(
                source=source, corp_id=str(corp_id)
            )
            previous_dept_count = DingTalkDirectoryDepartment.objects.filter(
                source=source, corp_id=str(corp_id), is_deleted=False
            ).count()
            previous_user_count = DingTalkDirectoryUser.objects.filter(
                source=source, corp_id=str(corp_id), is_deleted=False
            ).count()
            for department in departments:
                seen_depts.add(department["dept_id"])
                DingTalkDirectoryDepartment.objects.update_or_create(
                    source=source,
                    corp_id=str(corp_id),
                    dept_id=department["dept_id"],
                    defaults={
                        "name": department["name"],
                        "parent_dept_id": department["parent_dept_id"],
                        "raw": department["raw"],
                        "is_deleted": False,
                        "last_seen_at": started_at,
                    },
                )
                counters["departments"] += 1
            for user in users_by_id.values():
                seen_users.add(user["user_id"])
                DingTalkDirectoryUser.objects.update_or_create(
                    source=source,
                    corp_id=str(corp_id),
                    user_id=user["user_id"],
                    defaults={
                        **user,
                        "is_deleted": False,
                        "last_seen_at": started_at,
                    },
                )
                counters["users"] += 1

            # C2: skip orphan soft-deletion when the fetch is empty but the cache was populated,
            # so an incomplete-but-"successful" sync cannot wipe the directory. The synthetic
            # root department is excluded when judging whether any real department was seen.
            seen_real_dept_count = len(seen_depts - {"1"})
            if _should_skip_orphan_deletion(seen_real_dept_count, previous_dept_count):
                warnings.append(
                    "Skipped department deletion: the sync returned no departments while "
                    f"{previous_dept_count} were previously cached (treated as incomplete)."
                )
            else:
                DingTalkDirectoryDepartment.objects.filter(
                    source=source, corp_id=str(corp_id)
                ).exclude(dept_id__in=seen_depts).update(is_deleted=True)
            if _should_skip_orphan_deletion(len(seen_users), previous_user_count):
                warnings.append(
                    "Skipped user deletion: the sync returned no users while "
                    f"{previous_user_count} were previously cached (treated as incomplete)."
                )
            else:
                DingTalkDirectoryUser.objects.filter(source=source, corp_id=str(corp_id)).exclude(
                    user_id__in=seen_users
                ).update(is_deleted=True)
            counters["warnings"] = warnings
            status.status = "success"
            status.error = ""
            status.counters = counters
            status.finished_at = now()
            status.save()
        return counters
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
