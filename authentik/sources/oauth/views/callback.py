"""OAuth Callback Views"""

from datetime import timedelta
from json import JSONDecodeError
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django.views.generic import View
from guardian.shortcuts import get_anonymous_user
from structlog.stdlib import get_logger

from authentik.core.models import UserTypes
from authentik.core.sources.flow_manager import SourceFlowManager
from authentik.events.models import Event, EventAction
from authentik.flows.exceptions import FlowNonApplicableException
from authentik.flows.planner import PLAN_CONTEXT_SOURCE
from authentik.policies.engine import PolicyEngine
from authentik.policies.exceptions import PolicyEngineException
from authentik.policies.types import PolicyResult
from authentik.policies.utils import delete_none_values
from authentik.sources.oauth.clients.base import BaseOAuthClient
from authentik.sources.oauth.models import (
    GroupOAuthSourceConnection,
    OAuthSource,
    UserOAuthSourceConnection,
)
from authentik.sources.oauth.views.base import OAuthClientMixin
from authentik.stages.prompt.stage import PLAN_CONTEXT_PROMPT

LOGGER = get_logger()


class OAuthCallback(OAuthClientMixin, View):
    "Base OAuth callback view."

    source: OAuthSource
    token: dict[str, Any] | None = None

    def dispatch(self, request: HttpRequest, *_, **kwargs) -> HttpResponse:
        """View Get handler"""
        slug = kwargs.get("source_slug", "")
        try:
            self.source = OAuthSource.objects.get(slug=slug)
        except OAuthSource.DoesNotExist:
            raise Http404(f"Unknown OAuth source '{slug}'.") from None

        if not self.source.enabled:
            raise Http404(f"Source {slug} is not enabled.")
        client = self.get_client(self.source, callback=self.get_callback_url(self.source))
        # Fetch access token
        self.token = client.get_access_token()
        if self.token is None:
            return self.handle_login_failure("Could not retrieve token.")
        if "error" in self.token:
            return self.handle_login_failure(self.token["error"])
        # Fetch profile info
        try:
            res = self.redirect_flow_manager(client)
        except ValueError as exc:
            # if we're authenticated and not in a source stage and this new flag is enabled,
            # just continue
            if self.request.user.is_authenticated:
                pass
            return self.handle_login_failure(exc.args[0])
        return res

    def redirect_flow_manager(self, client: BaseOAuthClient) -> HttpResponse:
        try:
            raw_info = client.get_profile_info(self.token)
            if raw_info is None:
                raise ValueError("Could not retrieve profile.")
        except JSONDecodeError as exc:
            Event.new(
                EventAction.CONFIGURATION_ERROR,
                message="Failed to JSON-decode profile.",
                raw_profile=exc.doc,
            ).from_http(self.request)
            raise ValueError("Could not retrieve profile.") from None
        identifier = self.get_user_id(info=raw_info)
        if identifier is None:
            raise ValueError("Could not determine id.")
        sfm = OAuthSourceFlowManager(
            source=self.source,
            request=self.request,
            identifier=identifier,
            user_info={
                "info": raw_info,
                "client": client,
                "token": self.token,
            },
            policy_context={
                "oauth_userinfo": raw_info,
            },
        )
        return sfm.get_flow(
            raw_info=raw_info,
            access_token=self.token.get("access_token"),
            refresh_token=self.token.get("refresh_token"),
            expires=self.token.get("expires_in"),
        )

    def get_callback_url(self, source: OAuthSource) -> str:
        "Return callback url if different than the current url."
        return ""

    def get_error_redirect(self, source: OAuthSource, reason: str) -> str:
        "Return url to redirect on login failure."
        return settings.LOGIN_URL

    def get_user_id(self, info: dict[str, Any]) -> str | None:
        """Return unique identifier from the profile info."""
        if "id" in info:
            return str(info["id"])
        return None

    def handle_login_failure(self, reason: str) -> HttpResponse:
        "Message user and redirect on error."
        LOGGER.warning("Authentication Failure", reason=reason)
        messages.error(
            self.request,
            # Translate the template first, then interpolate: wrapping the already-formatted
            # string in _() would look up a per-reason msgid that is never in the catalog, so
            # the message would always fall back to English.
            _("Authentication failed: {reason}").format(reason=reason),
        )
        return redirect(self.get_error_redirect(self.source, reason))


class OAuthSourceFlowManager(SourceFlowManager):
    """Flow manager for oauth sources"""

    user_connection_type = UserOAuthSourceConnection
    group_connection_type = GroupOAuthSourceConnection

    def source_policy_result(self) -> PolicyResult:
        """Evaluate policies bound directly to the source before deciding the source action."""
        user = self.request.user if self.request.user.is_authenticated else get_anonymous_user()
        engine = PolicyEngine(self.source, user, self.request)
        engine.use_cache = False
        engine.request.context.update(self.policy_context)
        engine.request.context.update(
            {
                PLAN_CONTEXT_SOURCE: self.source,
                PLAN_CONTEXT_PROMPT: delete_none_values(self.user_properties),
                "prompt_data": delete_none_values(self.user_properties),
            }
        )
        return engine.build().result

    def get_flow(self, **kwargs) -> HttpResponse:
        # D3: evaluate source-bound policies before deciding the action, but only in this OAuth
        # subclass — the core SourceFlowManager.get_flow is left unmodified (smaller merge
        # surface) and non-OAuth source types (SAML/Plex/...) keep upstream behavior.
        try:
            source_policy_result = self.source_policy_result()
        except PolicyEngineException as exc:
            self._logger.warning("failed to evaluate source policy", exc=exc)
            source_policy_result = PolicyResult(False, str(exc))
        if not source_policy_result.passing:
            if getattr(self.source, "provider_type", "") == "dingtalk":
                source_policy_result = PolicyResult(
                    False, *(_(message) for message in source_policy_result.messages)
                )
            return self.error_handler(FlowNonApplicableException(source_policy_result))
        return super().get_flow(**kwargs)

    def _dingtalk_source_link_denial(self) -> str | None:
        """Return a deny message when the DingTalk allowlist rejects this login, else None.

        DingTalk sources are whitelist-gated: only a login matching an enabled, parseable
        managed allowlist is accepted. A source without a configured allowlist (or with an
        unparseable or disabled one) fails closed instead of silently allowing every corp.
        """
        if getattr(self.source, "provider_type", "") != "dingtalk":
            return None
        from authentik.sources.oauth.types.dingtalk import (
            DINGTALK_ALLOWLIST_PLAN_CONTEXT,
            build_dingtalk_allowlist_session_marker,
            dingtalk_allowlist_has_unparseable_binding,
            get_dingtalk_allowlist_binding,
        )

        # Named placeholders: unpacking into `_` would shadow gettext in this scope.
        _binding, _policy, config = get_dingtalk_allowlist_binding(self.source)
        if config is None:
            # B5: a managed allowlist that exists but cannot be parsed must fail closed and
            # alert an admin, rather than silently allowing every DingTalk user.
            if dingtalk_allowlist_has_unparseable_binding(self.source):
                Event.new(
                    EventAction.CONFIGURATION_ERROR,
                    message=(
                        "DingTalk allowlist policy exists but its config is unparseable; "
                        "denying DingTalk source login until it is repaired."
                    ),
                    source=self.source,
                ).from_http(self.request)
                return _(
                    "DingTalk login failed: the company allowlist is invalid. "
                    "Contact your administrator."
                )
            Event.new(
                EventAction.CONFIGURATION_ERROR,
                message=(
                    "No enabled DingTalk allowlist is configured for this source; "
                    "denying the DingTalk login. Save an allowlist on the source's "
                    "DingTalk Allowlist tab to permit sign-ins."
                ),
                source=self.source,
            ).from_http(self.request)
            return _(
                "DingTalk login failed: no company allowlist is configured. "
                "Contact your administrator."
            )
        userinfo = self.policy_context.get("oauth_userinfo") or {}
        marker = build_dingtalk_allowlist_session_marker(
            config,
            userinfo,
            self.source,
            self.identifier,
        )
        if not marker:
            self.policy_context.pop(DINGTALK_ALLOWLIST_PLAN_CONTEXT, None)
            return _(
                "DingTalk login failed: your company or department is not allowed. "
                "Contact your administrator."
            )
        self.policy_context[DINGTALK_ALLOWLIST_PLAN_CONTEXT] = marker
        return None

    def _dingtalk_allowlist_denied_response(self) -> HttpResponse | None:
        """Return a deny response when a source-bound DingTalk allowlist rejects this login."""
        denial = self._dingtalk_source_link_denial()
        if denial is None:
            return None
        return self.error_handler(Exception(denial))

    def _dingtalk_promote_user_to_internal(self, connection: UserOAuthSourceConnection) -> None:
        """Promote DingTalk-linked users to internal so they can use the user interface.

        Users enrolled before the DingTalk source set the user type were created as
        external and are locked out of /if/user/ when the brand has no default
        application."""
        if getattr(self.source, "provider_type", "") != "dingtalk":
            return
        user = getattr(connection, "user", None)
        if not user or not user.pk or user.type != UserTypes.EXTERNAL:
            return
        user.type = UserTypes.INTERNAL
        user.save(update_fields=["type"])
        # B13: the type change is a login-triggered, persistent permission change that
        # otherwise bypasses audit; record it explicitly.
        Event.new(
            EventAction.MODEL_UPDATED,
            message=(
                f"Promoted DingTalk user '{user.username}' from external to internal "
                "so they can use the user interface."
            ),
            source=self.source,
        ).from_http(self.request)

    def handle_existing_link(self, connection: UserOAuthSourceConnection) -> HttpResponse:
        if denied_response := self._dingtalk_allowlist_denied_response():
            return denied_response
        self._dingtalk_promote_user_to_internal(connection)
        return super().handle_existing_link(connection)

    def handle_auth(self, connection: UserOAuthSourceConnection) -> HttpResponse:
        if denied_response := self._dingtalk_allowlist_denied_response():
            return denied_response
        self._dingtalk_promote_user_to_internal(connection)
        return super().handle_auth(connection)

    def _dingtalk_require_userid_for_enroll(self) -> HttpResponse | None:
        """Fail closed when enrolling a DingTalk user whose userid is unavailable.

        The username must be the DingTalk userid; if the best-effort directory enhancement did
        not return one we cannot provision a stable account, so deny enrollment with a clear
        message rather than creating an account with a missing/derived username (see B2).
        """
        if getattr(self.source, "provider_type", "") != "dingtalk":
            return None
        userinfo = self.policy_context.get("oauth_userinfo") or {}
        if userinfo.get("userid") or userinfo.get("userId"):
            return None
        return self.error_handler(
            Exception(
                _(
                    "DingTalk login failed: your DingTalk user information is temporarily "
                    "unavailable. Try again later or contact your administrator."
                )
            )
        )

    def handle_enroll(self, connection: UserOAuthSourceConnection) -> HttpResponse:
        if denied_response := self._dingtalk_allowlist_denied_response():
            return denied_response
        if userid_guard := self._dingtalk_require_userid_for_enroll():
            return userid_guard
        return super().handle_enroll(connection)

    def update_user_connection(
        self,
        connection: UserOAuthSourceConnection,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_in: int | None = None,
        **_,
    ) -> UserOAuthSourceConnection:
        """Set the access_token and refresh_token on the connection"""
        connection.access_token = access_token
        connection.refresh_token = refresh_token
        connection.expires = now() + timedelta(seconds=expires_in) if expires_in else now()
        return connection
