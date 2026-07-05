"""Signals for the user_login stage."""

from django.dispatch import Signal

# Sent by the user_login stage right after the user is logged in, before the session response
# is finalized. Lets other apps persist per-login session state (e.g. the DingTalk allowlist
# marker) without the core stage importing app-specific code.
#
# Providing kwargs: ``request`` (HttpRequest), ``user`` (User), ``stage_view`` (the
# UserLoginStageView, exposing ``executor.plan.context``). Receivers may mutate
# ``request.session`` and should set ``request.session.modified = True``.
user_login_session_finalized = Signal()
