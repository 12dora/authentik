"""Read-only DingTalk directory selectors for property mappings."""

from datetime import timedelta
from typing import Any

from django.utils.timezone import now

from authentik.core.models import User
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)

MAX_MANAGER_CHAIN_DEPTH = 20
STALE_AFTER = timedelta(hours=24)


def empty_org_context(source_slug: str) -> dict[str, Any]:
    return {
        "corp_id": None,
        "user_id": None,
        "source_slug": source_slug,
        "departments": [],
        "manager": None,
        "manager_chain": [],
        "stale": True,
        "last_synced_at": None,
    }


def _public_user(user: DingTalkDirectoryUser) -> dict[str, Any]:
    return {
        "user_id": user.user_id,
        "name": user.name,
        "title": user.title,
        "avatar": user.avatar,
    }


def _department_path(source: OAuthSource, corp_id: str, dept_id: str) -> list[dict[str, str]]:
    departments = {
        item.dept_id: item
        for item in DingTalkDirectoryDepartment.objects.filter(
            source=source,
            corp_id=corp_id,
            is_deleted=False,
        )
    }
    path = []
    seen = set()
    current = departments.get(dept_id)
    while current and current.dept_id not in seen:
        seen.add(current.dept_id)
        path.append({"dept_id": current.dept_id, "name": current.name})
        current = departments.get(current.parent_dept_id)
    return list(reversed(path))


def _manager_chain(
    source: OAuthSource, corp_id: str, start: DingTalkDirectoryUser
) -> list[dict[str, Any]]:
    chain = []
    seen = {start.user_id}
    manager_user_id = start.manager_user_id
    for _ in range(MAX_MANAGER_CHAIN_DEPTH):
        if not manager_user_id or manager_user_id in seen:
            break
        manager = DingTalkDirectoryUser.objects.filter(
            source=source,
            corp_id=corp_id,
            user_id=manager_user_id,
            is_deleted=False,
        ).first()
        if not manager:
            break
        seen.add(manager.user_id)
        chain.append(_public_user(manager))
        manager_user_id = manager.manager_user_id
    return chain


def get_dingtalk_org_context(
    user: User,
    source_slug: str = "dingtalk",
    include_manager_chain: bool = True,
    include_department_path: bool = True,
) -> dict[str, Any]:
    """Return JSON-safe current-user DingTalk organization context from local cache only."""
    context = empty_org_context(source_slug)
    dingtalk = (user.attributes or {}).get("dingtalk") or {}
    corp_id = dingtalk.get("corp_id") or dingtalk.get("corpId")
    user_id = dingtalk.get("user_id") or dingtalk.get("userid") or dingtalk.get("userId")
    if not corp_id or not user_id:
        return context
    source = OAuthSource.objects.filter(
        slug=source_slug, provider_type="dingtalk", enabled=True
    ).first()
    if not source:
        return context
    status = DingTalkDirectorySyncStatus.objects.filter(source=source, corp_id=str(corp_id)).first()
    last_synced_at = status.finished_at if status else None
    stale = not last_synced_at or last_synced_at < now() - STALE_AFTER
    directory_user = DingTalkDirectoryUser.objects.filter(
        source=source,
        corp_id=str(corp_id),
        user_id=str(user_id),
        is_deleted=False,
    ).first()
    context.update(
        {
            "corp_id": str(corp_id),
            "user_id": str(user_id),
            "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
            "stale": stale,
        }
    )
    if not directory_user:
        return context
    departments = []
    for dept_id in directory_user.dept_id_list:
        department = DingTalkDirectoryDepartment.objects.filter(
            source=source,
            corp_id=str(corp_id),
            dept_id=str(dept_id),
            is_deleted=False,
        ).first()
        if not department:
            continue
        value = {
            "dept_id": department.dept_id,
            "name": department.name,
            "parent_id": department.parent_dept_id,
        }
        if include_department_path:
            value["path"] = _department_path(source, str(corp_id), department.dept_id)
        departments.append(value)
    chain = _manager_chain(source, str(corp_id), directory_user) if include_manager_chain else []
    context["departments"] = departments
    context["manager_chain"] = chain
    context["manager"] = chain[0] if chain else None
    return context
