"""Read-only DingTalk managed-user resolver."""

from typing import Any

from django.utils.timezone import now
from structlog.stdlib import get_logger

from authentik.sources.oauth.dingtalk.selectors import MAX_MANAGER_CHAIN_DEPTH, STALE_AFTER
from authentik.sources.oauth.models import (
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
    UserOAuthSourceConnection,
)

LOGGER = get_logger()
RESOLVER = "dingtalk_manager_chain"


class DingTalkManagerNotFound(ValueError):
    """Raised when the requested DingTalk manager is not present in the local cache."""


class DingTalkBindingConflict(ValueError):
    """Raised when a DingTalk source identifier maps to multiple authentik users."""


class DingTalkSourceUnavailable(ValueError):
    """Raised when the requested DingTalk source is not available for resolution."""


def _sync_status(source: OAuthSource, corp_id: str) -> DingTalkDirectorySyncStatus | None:
    return DingTalkDirectorySyncStatus.objects.filter(
        source=source,
        corp_id=corp_id,
    ).first()


def _is_stale(status: DingTalkDirectorySyncStatus | None) -> bool:
    return (
        not status
        or status.status != "success"
        or not status.finished_at
        or status.finished_at < now() - STALE_AFTER
    )


def _descendants(
    source: OAuthSource,
    corp_id: str,
    manager_user_id: str,
) -> tuple[list[DingTalkDirectoryUser], dict[str, bool]]:
    users_by_manager: dict[str, list[DingTalkDirectoryUser]] = {}
    for user in DingTalkDirectoryUser.objects.filter(
        source=source,
        corp_id=corp_id,
        is_deleted=False,
    ).order_by("user_id"):
        users_by_manager.setdefault(user.manager_user_id, []).append(user)

    result: list[DingTalkDirectoryUser] = []
    seen = {manager_user_id}
    diagnostics = {
        "recursion_cycle_detected": False,
        "max_depth_exceeded": False,
    }

    def visit(parent_user_id: str, depth: int) -> None:
        children = users_by_manager.get(parent_user_id, [])
        if depth >= MAX_MANAGER_CHAIN_DEPTH:
            if children:
                diagnostics["max_depth_exceeded"] = True
            return
        for user in children:
            if user.user_id in seen:
                diagnostics["recursion_cycle_detected"] = True
                continue
            seen.add(user.user_id)
            result.append(user)
            visit(user.user_id, depth + 1)

    visit(manager_user_id, 0)
    return result, diagnostics


def _binding_for(
    source: OAuthSource,
    corp_id: str,
    manager_user_id: str,
    source_identifier: str,
) -> dict[str, Any]:
    connections = list(
        UserOAuthSourceConnection.objects.filter(
            source=source,
            identifier=source_identifier,
        )
        .select_related("user")
        .order_by("pk")[:2]
    )
    if len(connections) > 1:
        LOGGER.warning(
            "dingtalk_managed_users_binding_conflict",
            source_slug=source.slug,
            corp_id=corp_id,
            manager_user_id=manager_user_id,
            source_identifier=source_identifier,
        )
        raise DingTalkBindingConflict(
            f"Multiple user OAuth connections found for {source.slug}:{source_identifier}"
        )
    if not connections:
        LOGGER.info(
            "dingtalk_managed_users_unbound_user",
            source_slug=source.slug,
            corp_id=corp_id,
            manager_user_id=manager_user_id,
            source_identifier=source_identifier,
        )
        return {
            "authentik_subject": None,
            "authentik_subject_type": None,
            "authentik_user_active": None,
            "binding_status": "unbound",
            "diagnostics": {},
        }
    user = connections[0].user
    return {
        "authentik_subject": user.uid,
        "authentik_subject_type": "user_uid",
        "authentik_user_active": user.is_active,
        "binding_status": "bound",
        "diagnostics": {"authentik_user_pk": user.pk},
    }


def get_dingtalk_managed_users(
    source: OAuthSource,
    corp_id: str,
    manager_user_id: str,
) -> dict[str, Any]:
    """Return cached DingTalk users recursively managed by the given manager."""
    corp_id = str(corp_id)
    manager_user_id = str(manager_user_id)
    if not source.enabled:
        raise DingTalkSourceUnavailable(
            f"DingTalk source {source.slug!r} is disabled and cannot resolve managed users"
        )
    status = _sync_status(source, corp_id)
    manager = DingTalkDirectoryUser.objects.filter(
        source=source,
        corp_id=corp_id,
        user_id=manager_user_id,
        is_deleted=False,
    ).first()
    if not manager:
        raise DingTalkManagerNotFound(
            f"DingTalk manager {manager_user_id!r} was not found for {source.slug}:{corp_id}"
        )

    users = []
    descendants, diagnostics = _descendants(source, corp_id, manager.user_id)
    if diagnostics["recursion_cycle_detected"] or diagnostics["max_depth_exceeded"]:
        LOGGER.warning(
            "dingtalk_managed_users_recursion_diagnostics",
            source_slug=source.slug,
            corp_id=corp_id,
            manager_user_id=manager.user_id,
            recursion_cycle_detected=diagnostics["recursion_cycle_detected"],
            max_depth_exceeded=diagnostics["max_depth_exceeded"],
        )
    for directory_user in descendants:
        source_identifier = f"{corp_id}:{directory_user.user_id}"
        users.append(
            {
                "source_user_id": directory_user.user_id,
                "source_identifier": source_identifier,
                "directory_active": directory_user.active,
                "is_deleted": directory_user.is_deleted,
                **_binding_for(source, corp_id, manager.user_id, source_identifier),
            }
        )
    stale = _is_stale(status)
    if stale:
        LOGGER.info(
            "dingtalk_managed_users_stale_cache",
            source_slug=source.slug,
            corp_id=corp_id,
            manager_user_id=manager.user_id,
            sync_status=status.status if status else None,
            last_synced_at=status.finished_at.isoformat()
            if status and status.finished_at
            else None,
        )

    return {
        "source_slug": source.slug,
        "corp_id": corp_id,
        "manager_user_id": manager.user_id,
        "resolver": RESOLVER,
        "resolved_at": now().isoformat(),
        "stale": stale,
        "last_synced_at": status.finished_at.isoformat()
        if status and status.finished_at
        else None,
        "diagnostics": diagnostics,
        "users": users,
    }
