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
        "max_depth_omitted": 0,
    }

    def visit(parent_user_id: str, depth: int) -> None:
        children = users_by_manager.get(parent_user_id, [])
        if depth >= MAX_MANAGER_CHAIN_DEPTH:
            if children:
                diagnostics["max_depth_exceeded"] = True
                # Report how many not-yet-seen direct reports were dropped at the depth
                # boundary so callers know the result is truncated (their subordinates are
                # dropped too) rather than silently getting a short list — C7.
                diagnostics["max_depth_omitted"] += sum(
                    1 for child in children if child.user_id not in seen
                )
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


def _bindings_by_identity(
    source: OAuthSource, identities: list[str]
) -> dict[str, list[UserOAuthSourceConnection]]:
    """Resolve all OAuth connections for the given identities in a single query (avoids N+1)."""
    bindings: dict[str, list[UserOAuthSourceConnection]] = {}
    identities = [identity for identity in identities if identity]
    if not identities:
        return bindings
    for connection in (
        UserOAuthSourceConnection.objects.filter(source=source, identifier__in=identities)
        .select_related("user")
        .order_by("pk")
    ):
        bindings.setdefault(connection.identifier, []).append(connection)
    return bindings


def _binding_for(
    source: OAuthSource,
    corp_id: str,
    manager_user_id: str,
    directory_user: DingTalkDirectoryUser,
    bindings_by_identity: dict[str, list[UserOAuthSourceConnection]],
) -> dict[str, Any]:
    # UserOAuthSourceConnection.identifier is the DingTalk unionId (see get_user_id), so match
    # on the directory user's cached unionId rather than the corp:userid display identifier.
    identity = directory_user.union_id
    connections = bindings_by_identity.get(identity, []) if identity else []
    source_identifier = f"{corp_id}:{directory_user.user_id}"
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
            max_depth_omitted=diagnostics["max_depth_omitted"],
        )
    # C6: resolve every subordinate's OAuth connection in a single query keyed by unionId.
    bindings_by_identity = _bindings_by_identity(
        source, [directory_user.union_id for directory_user in descendants]
    )
    for directory_user in descendants:
        source_identifier = f"{corp_id}:{directory_user.user_id}"
        users.append(
            {
                "source_user_id": directory_user.user_id,
                "source_identifier": source_identifier,
                "directory_active": directory_user.active,
                "is_deleted": directory_user.is_deleted,
                **_binding_for(
                    source, corp_id, manager.user_id, directory_user, bindings_by_identity
                ),
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
