"""Read-only DingTalk managed-user resolver."""

from typing import Any

from django.core.paginator import EmptyPage, Paginator
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
DEFAULT_MANAGED_USERS_PAGE_SIZE = 100
MAX_MANAGED_USERS_PAGE_SIZE = 100
MAX_MANAGED_USERS_RESULTS = 1000


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
        or not status.last_success_at
        or status.last_success_at < now() - STALE_AFTER
    )


def _descendants(
    source: OAuthSource,
    corp_id: str,
    manager_user_id: str,
    *,
    max_results: int = MAX_MANAGED_USERS_RESULTS,
) -> tuple[list[DingTalkDirectoryUser], dict[str, Any]]:
    result: list[DingTalkDirectoryUser] = []
    seen = {manager_user_id}
    diagnostics = {
        "recursion_cycle_detected": False,
        "max_depth_exceeded": False,
        "max_depth_omitted": 0,
        "result_limit_exceeded": False,
        "result_limit": max_results,
    }
    users_by_manager: dict[str, list[DingTalkDirectoryUser]] = {}

    current_manager_ids = [manager_user_id]
    for depth in range(MAX_MANAGER_CHAIN_DEPTH + 1):
        if not current_manager_ids:
            break
        children = list(
            DingTalkDirectoryUser.objects.filter(
                source=source,
                corp_id=corp_id,
                manager_user_id__in=current_manager_ids,
                is_deleted=False,
            ).order_by("manager_user_id", "user_id")
        )
        if depth >= MAX_MANAGER_CHAIN_DEPTH:
            omitted = sum(1 for child in children if child.user_id not in seen)
            if omitted:
                diagnostics["max_depth_exceeded"] = True
                diagnostics["max_depth_omitted"] += omitted
            break

        next_manager_ids = []
        for child in children:
            if child.user_id in seen:
                diagnostics["recursion_cycle_detected"] = True
                continue
            if len(seen) - 1 >= max_results:
                diagnostics["result_limit_exceeded"] = True
                break
            seen.add(child.user_id)
            users_by_manager.setdefault(child.manager_user_id, []).append(child)
            next_manager_ids.append(child.user_id)
        if diagnostics["result_limit_exceeded"]:
            break
        current_manager_ids = next_manager_ids

    seen = {manager_user_id}
    stack = list(reversed(users_by_manager.get(manager_user_id, [])))
    while stack:
        child = stack.pop()
        if child.user_id in seen:
            diagnostics["recursion_cycle_detected"] = True
            continue
        seen.add(child.user_id)
        result.append(child)
        stack.extend(reversed(users_by_manager.get(child.user_id, [])))

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
    *,
    page: int = 1,
    page_size: int = DEFAULT_MANAGED_USERS_PAGE_SIZE,
    max_results: int = MAX_MANAGED_USERS_RESULTS,
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

    page = max(int(page), 1)
    page_size = max(1, min(int(page_size), MAX_MANAGED_USERS_PAGE_SIZE))
    max_results = max(1, min(int(max_results), MAX_MANAGED_USERS_RESULTS))
    users = []
    descendants, diagnostics = _descendants(
        source, corp_id, manager.user_id, max_results=max_results
    )
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
    paginator = Paginator(descendants, page_size)
    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages or 1)
    page_descendants = list(page_obj.object_list)
    bindings_by_identity = _bindings_by_identity(
        source, [directory_user.union_id for directory_user in page_descendants]
    )
    for directory_user in page_descendants:
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
            last_synced_at=status.last_success_at.isoformat()
            if status and status.last_success_at
            else None,
        )

    return {
        "source_slug": source.slug,
        "corp_id": corp_id,
        "manager_user_id": manager.user_id,
        "resolver": RESOLVER,
        "resolved_at": now().isoformat(),
        "stale": stale,
        "last_synced_at": status.last_success_at.isoformat()
        if status and status.last_success_at
        else None,
        "diagnostics": diagnostics,
        "pagination": {
            "next": page_obj.next_page_number() if page_obj.has_next() else 0,
            "previous": page_obj.previous_page_number() if page_obj.has_previous() else 0,
            "count": paginator.count,
            "current": page_obj.number,
            "total_pages": paginator.num_pages,
            "page_size": page_size,
        },
        "users": users,
    }
