"""Read-only DingTalk directory selectors for property mappings."""

from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils.timezone import now

from authentik.core.models import User
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectorySyncStatusChoices,
    DingTalkDirectoryUser,
    OAuthSource,
    UserOAuthSourceConnection,
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


def _department_path(
    departments: dict[str, DingTalkDirectoryDepartment], dept_id: str
) -> list[dict[str, str]]:
    path = []
    seen = set()
    current = departments.get(dept_id)
    while current and current.dept_id not in seen:
        seen.add(current.dept_id)
        path.append({"dept_id": current.dept_id, "name": current.name})
        current = departments.get(current.parent_dept_id)
    return list(reversed(path))


def _manager_chain(
    users_by_user_id: dict[str, DingTalkDirectoryUser], start: DingTalkDirectoryUser
) -> list[dict[str, Any]]:
    chain = []
    seen = {start.user_id}
    manager_user_id = start.manager_user_id
    for _ in range(MAX_MANAGER_CHAIN_DEPTH):
        if not manager_user_id or manager_user_id in seen:
            break
        manager = users_by_user_id.get(manager_user_id)
        if not manager:
            break
        seen.add(manager.user_id)
        chain.append(_public_user(manager))
        manager_user_id = manager.manager_user_id
    return chain


def _identity_from_profile(profile: Any) -> tuple[str, str] | None:
    if not isinstance(profile, dict):
        return None
    corp_id = profile.get("corp_id") or profile.get("corpId")
    user_id = profile.get("user_id") or profile.get("userid") or profile.get("userId")
    if not corp_id or not user_id:
        return None
    return str(corp_id), str(user_id)


def source_scoped_dingtalk_identity(
    user: User,
    source: OAuthSource,
) -> tuple[str, str] | None:
    """Resolve a user's DingTalk corp/user identity for one source.

    The legacy profile attribute is global across DingTalk sources. Prefer the canonical
    source-keyed dict written by DingTalkType, then tolerate the transitional source-stamped list
    shape, then source connection/cache identity, before falling back to the legacy bucket for
    older profiles.
    """
    attributes = user.attributes or {}
    dingtalk_sources = attributes.get("dingtalk_sources")
    if isinstance(dingtalk_sources, dict):
        identity = _identity_from_profile(dingtalk_sources.get(str(source.pk)))
        if identity:
            return identity
    elif isinstance(dingtalk_sources, list):
        source_pk = str(source.pk)
        for profile in reversed(dingtalk_sources):
            if not isinstance(profile, dict) or str(profile.get("source_pk")) != source_pk:
                continue
            identity = _identity_from_profile(profile)
            if identity:
                return identity

    connection = UserOAuthSourceConnection.objects.filter(user=user, source=source).first()
    if connection and connection.identifier:
        directory_user = (
            DingTalkDirectoryUser.objects.filter(source=source, is_deleted=False)
            .filter(Q(union_id=connection.identifier) | Q(open_id=connection.identifier))
            .order_by("-last_seen_at", "corp_id", "user_id")
            .first()
        )
        if directory_user:
            return directory_user.corp_id, directory_user.user_id

    identity = _identity_from_profile(attributes.get("dingtalk"))
    if not identity:
        return None
    corp_id, user_id = identity
    exists = DingTalkDirectoryUser.objects.filter(
        source=source,
        corp_id=corp_id,
        user_id=user_id,
        is_deleted=False,
    ).exists()
    if not exists:
        return None
    return corp_id, user_id


def get_dingtalk_org_context(
    user: User,
    source_slug: str = "dingtalk",
    include_manager_chain: bool = True,
    include_department_path: bool = True,
) -> dict[str, Any]:
    """Return JSON-safe current-user DingTalk organization context from local cache only."""
    context = empty_org_context(source_slug)
    source = OAuthSource.objects.filter(
        slug=source_slug, provider_type="dingtalk", enabled=True
    ).first()
    if not source:
        return context
    identity = source_scoped_dingtalk_identity(user, source)
    if not identity:
        return context
    corp_id, user_id = identity
    status = DingTalkDirectorySyncStatus.objects.filter(source=source, corp_id=str(corp_id)).first()
    last_synced_at = (
        status.last_success_at
        if status and status.status == DingTalkDirectorySyncStatusChoices.SUCCESS
        else None
    )
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
    departments_by_id = {
        item.dept_id: item
        for item in DingTalkDirectoryDepartment.objects.filter(
            source=source,
            corp_id=str(corp_id),
            is_deleted=False,
        )
    }
    departments = []
    for dept_id in directory_user.dept_id_list:
        department = departments_by_id.get(str(dept_id))
        if not department:
            continue
        value = {
            "dept_id": department.dept_id,
            "name": department.name,
            "parent_id": department.parent_dept_id,
        }
        if include_department_path:
            value["path"] = _department_path(departments_by_id, department.dept_id)
        departments.append(value)
    users_by_user_id = {}
    if include_manager_chain:
        users_by_user_id = {
            item.user_id: item
            for item in DingTalkDirectoryUser.objects.filter(
                source=source,
                corp_id=str(corp_id),
                is_deleted=False,
            )
        }
    chain = _manager_chain(users_by_user_id, directory_user) if include_manager_chain else []
    context["departments"] = departments
    context["manager_chain"] = chain
    context["manager"] = chain[0] if chain else None
    return context
