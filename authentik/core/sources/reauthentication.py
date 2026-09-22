"""Short-lived marker for a provider re-authentication that is still in progress.

OIDC ``prompt=login`` / ``max_age`` and SAML ForceAuthn send the browser through
authentication without logging the Django session out. The provider's own
``last_login_uid`` keys stay until some later login and are not a signal that a
source callback must ignore the session user. This marker is that signal.
Login and cancelling the flow clear it.
"""

from enum import StrEnum
from time import time

from django.http import HttpRequest

from authentik.events.signals import get_login_event

SESSION_KEY_SOURCE_REAUTHENTICATION = "authentik/core/sources/reauthentication"
# Seconds. Long enough to finish an interactive source login, short enough that
# an abandoned prompt does not keep changing source linking.
SOURCE_REAUTHENTICATION_TTL = 10 * 60


class SourceReauthenticationState(StrEnum):
    """How a source callback should treat the session marker."""

    PENDING = "pending"
    STALE = "stale"
    NONE = "none"


def mark_source_reauthentication(request: HttpRequest) -> None:
    """Remember that this session still owes a provider a fresh login."""
    event = get_login_event(request)
    request.session[SESSION_KEY_SOURCE_REAUTHENTICATION] = {
        "login_uid": str(event.pk) if event is not None else None,
        "expires": time() + SOURCE_REAUTHENTICATION_TTL,
    }


def source_reauthentication_state(request: HttpRequest) -> SourceReauthenticationState:
    """Classify the source re-authentication marker.

    PENDING when the user is authenticated, the marker has not expired, and its
    login id still matches this session's login event. Both values being missing
    counts as a match (SAML ForceAuthn with no login event). STALE when a marker
    is present but expired or the login id does not match; that marker is
    removed. NONE when no marker is stored, or the request is unauthenticated
    (a still-valid marker is left in place).
    """
    marker = request.session.get(SESSION_KEY_SOURCE_REAUTHENTICATION)
    if not isinstance(marker, dict):
        if SESSION_KEY_SOURCE_REAUTHENTICATION in request.session:
            request.session.pop(SESSION_KEY_SOURCE_REAUTHENTICATION, None)
        return SourceReauthenticationState.NONE
    event = get_login_event(request)
    current_uid = str(event.pk) if event is not None else None
    expires = marker.get("expires")
    if (
        not isinstance(expires, int | float)
        or time() >= expires
        or marker.get("login_uid") != current_uid
    ):
        request.session.pop(SESSION_KEY_SOURCE_REAUTHENTICATION, None)
        return SourceReauthenticationState.STALE
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return SourceReauthenticationState.NONE
    return SourceReauthenticationState.PENDING


def source_reauthentication_pending(request: HttpRequest) -> bool:
    """Return whether a source callback must ignore the current session user."""
    return source_reauthentication_state(request) == SourceReauthenticationState.PENDING


def clear_source_reauthentication(request: HttpRequest) -> None:
    """Drop the marker after login, whether or not the user id changed."""
    request.session.pop(SESSION_KEY_SOURCE_REAUTHENTICATION, None)
