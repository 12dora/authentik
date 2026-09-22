"""OAuth source callback while a provider re-authentication marker is pending."""

from time import time
from unittest.mock import Mock, patch

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import TestCase

from authentik.core.models import SourceUserMatchingModes, User
from authentik.core.sources.matcher import Action
from authentik.core.sources.reauthentication import (
    SESSION_KEY_SOURCE_REAUTHENTICATION,
    SOURCE_REAUTHENTICATION_TTL,
    SourceReauthenticationState,
    clear_source_reauthentication,
    mark_source_reauthentication,
    source_reauthentication_pending,
    source_reauthentication_state,
)
from authentik.core.sources.stage import PLAN_CONTEXT_SOURCES_CONNECTION
from authentik.core.tests.utils import RequestFactory, create_test_flow, create_test_user
from authentik.events.models import Event, EventAction
from authentik.events.signals import SESSION_LOGIN_EVENT
from authentik.flows.models import FlowAuthenticationRequirement, FlowDesignation
from authentik.flows.planner import (
    PLAN_CONTEXT_PENDING_USER,
    PLAN_CONTEXT_SOURCE_REAUTHENTICATION,
)
from authentik.flows.views.executor import (
    SESSION_KEY_GET,
    SESSION_KEY_PLAN,
    CancelView,
    FlowExecutorView,
)
from authentik.lib.generators import generate_id
from authentik.policies.denied import AccessDeniedResponse
from authentik.sources.oauth.models import OAuthSource, UserOAuthSourceConnection
from authentik.sources.oauth.views.callback import OAuthSourceFlowManager

IDENTIFIER = "union-id-b"


class TestSourceReauthenticationMarker(TestCase):
    """The short-lived marker, not the provider last_login_uid keys."""

    def setUp(self):
        self.user = create_test_user()
        self.factory = RequestFactory()

    def _request(self, user=None, *, login_event: bool = True):
        request = self.factory.get("/", user=user)
        if login_event:
            event = Event.new(EventAction.LOGIN)
            event.save()
            request.session[SESSION_LOGIN_EVENT] = event
        return request

    def test_mark_matches_current_login(self):
        request = self._request(self.user)
        before = time()
        mark_source_reauthentication(request)
        marker = request.session[SESSION_KEY_SOURCE_REAUTHENTICATION]
        login_uid = str(request.session[SESSION_LOGIN_EVENT].pk)

        self.assertEqual(marker["login_uid"], login_uid)
        self.assertGreaterEqual(marker["expires"], before + SOURCE_REAUTHENTICATION_TTL)
        self.assertLess(marker["expires"], time() + SOURCE_REAUTHENTICATION_TTL + 5)
        self.assertEqual(
            source_reauthentication_state(request),
            SourceReauthenticationState.PENDING,
        )
        self.assertTrue(source_reauthentication_pending(request))

    def test_expired_marker_is_removed(self):
        request = self._request(self.user)
        mark_source_reauthentication(request)
        request.session[SESSION_KEY_SOURCE_REAUTHENTICATION]["expires"] = time() - 1

        self.assertEqual(source_reauthentication_state(request), SourceReauthenticationState.STALE)
        self.assertFalse(source_reauthentication_pending(request))
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)

    def test_mismatched_login_uid_is_removed(self):
        request = self._request(self.user)
        mark_source_reauthentication(request)
        marker = request.session[SESSION_KEY_SOURCE_REAUTHENTICATION]
        marker["login_uid"] = "other-login"
        request.session[SESSION_KEY_SOURCE_REAUTHENTICATION] = marker

        self.assertEqual(source_reauthentication_state(request), SourceReauthenticationState.STALE)
        self.assertFalse(source_reauthentication_pending(request))
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)

    def test_unauthenticated_request_is_not_pending(self):
        request = self._request(self.user)
        mark_source_reauthentication(request)
        request.user = AnonymousUser()

        self.assertEqual(source_reauthentication_state(request), SourceReauthenticationState.NONE)
        self.assertFalse(source_reauthentication_pending(request))
        self.assertIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)

    def test_absent_login_event_matches_none(self):
        """SAML ForceAuthn can mark a session that has no login event."""
        request = self._request(self.user, login_event=False)
        mark_source_reauthentication(request)

        self.assertIsNone(request.session[SESSION_KEY_SOURCE_REAUTHENTICATION]["login_uid"])
        self.assertEqual(
            source_reauthentication_state(request),
            SourceReauthenticationState.PENDING,
        )
        self.assertTrue(source_reauthentication_pending(request))

    def test_clear_removes_marker(self):
        request = self._request(self.user)
        mark_source_reauthentication(request)
        clear_source_reauthentication(request)

        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)
        self.assertEqual(source_reauthentication_state(request), SourceReauthenticationState.NONE)
        self.assertFalse(source_reauthentication_pending(request))

    def test_executor_cancel_clears_marker(self):
        """Cancelling the flow executor drops the marker with the plan."""
        request = self._request(self.user)
        mark_source_reauthentication(request)
        request.session[SESSION_KEY_PLAN] = "plan"
        request.session[SESSION_KEY_GET] = {"next": "/"}
        view = FlowExecutorView()
        view.request = request
        view._logger = Mock()

        view.cancel()

        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)
        self.assertNotIn(SESSION_KEY_PLAN, request.session)
        self.assertNotIn(SESSION_KEY_GET, request.session)

    def test_cancel_view_clears_marker(self):
        """The cancel URL drops the marker from the session it leaves behind."""
        request = self._request(self.user)
        mark_source_reauthentication(request)
        request.session[SESSION_KEY_PLAN] = "plan"
        view = CancelView()
        view.request = request

        response = view.get(request)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)
        self.assertNotIn(SESSION_KEY_PLAN, request.session)


class TestOAuthCallbackReauthentication(TestCase):
    """Pending re-auth follows the anonymous matcher and still plans authentication."""

    def setUp(self):
        self.auth_flow = create_test_flow(FlowDesignation.AUTHENTICATION)
        self.auth_flow.authentication = FlowAuthenticationRequirement.REQUIRE_UNAUTHENTICATED
        self.auth_flow.save()
        self.enroll_flow = create_test_flow(FlowDesignation.ENROLLMENT)
        self.source = OAuthSource.objects.create(
            name=generate_id(),
            slug=generate_id(),
            provider_type="openidconnect",
            consumer_key=generate_id(),
            consumer_secret=generate_id(),
            authentication_flow=self.auth_flow,
            enrollment_flow=self.enroll_flow,
        )
        self.factory = RequestFactory()
        self.user_a = create_test_user("reauth-user-a")
        self.user_b = create_test_user("reauth-user-b")

    def _request(self, user, *, pending: bool = False):
        request = self.factory.get("/", user=user)
        event = Event.new(EventAction.LOGIN)
        event.save()
        request.session[SESSION_LOGIN_EVENT] = event
        if pending:
            mark_source_reauthentication(request)
        return request

    def _manager(self, request, info: dict | None = None) -> OAuthSourceFlowManager:
        return OAuthSourceFlowManager(
            self.source,
            request,
            IDENTIFIER,
            {"info": {"sub": IDENTIFIER, **(info or {})}},
            {},
        )

    def test_pending_existing_connection_plans_require_unauthenticated_flow(self):
        """B's existing connection authenticates B through the source authentication flow."""
        existing = UserOAuthSourceConnection.objects.create(
            user=self.user_b,
            source=self.source,
            identifier=IDENTIFIER,
        )
        request = self._request(self.user_a, pending=True)
        response = self._manager(request).get_flow(
            access_token="access-token",
            refresh_token="refresh-token",
            expires=120,
        )

        self.assertEqual(
            self.auth_flow.authentication,
            FlowAuthenticationRequirement.REQUIRE_UNAUTHENTICATED,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.auth_flow.slug, response.url)
        plan = request.session[SESSION_KEY_PLAN]
        self.assertEqual(plan.context[PLAN_CONTEXT_PENDING_USER].pk, self.user_b.pk)
        self.assertEqual(
            plan.context[PLAN_CONTEXT_SOURCE_REAUTHENTICATION],
            str(self.auth_flow.pk),
        )
        staged = plan.context[PLAN_CONTEXT_SOURCES_CONNECTION]
        self.assertEqual(staged.pk, existing.pk)
        self.assertEqual(staged.access_token, "access-token")
        existing.refresh_from_db()
        self.assertEqual(existing.user_id, self.user_b.pk)
        self.assertIsNone(existing.access_token)
        self.assertFalse(
            UserOAuthSourceConnection.objects.filter(user=self.user_a, source=self.source).exists()
        )

    def test_pending_email_link_authenticates_without_saving_connection(self):
        """An email link logs the matched account in and does not save the row yet."""
        self.source.user_matching_mode = SourceUserMatchingModes.EMAIL_LINK
        self.source.save()
        self.user_b.email = "shared@example.com"
        self.user_b.save()
        request = self._request(self.user_a, pending=True)
        manager = self._manager(request, {"email": "shared@example.com"})
        seen: list[tuple[str, int | None]] = []

        class Hook:
            def oauth_pre_auth(self, _manager, connection):
                seen.append(("auth", connection.user_id))

            def oauth_pre_existing_link(self, _manager, connection):
                seen.append(("link", connection.user_id))
                return HttpResponse("linked")

            def oauth_pre_enroll(self, _manager, connection):
                seen.append(("enroll", connection.user_id))
                return HttpResponse("enrolled")

        manager.source_type = Hook
        response = manager.get_flow(access_token="access-token", expires=120)

        self.assertEqual(seen, [("auth", self.user_b.pk)])
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.auth_flow.slug, response.url)
        self.assertNotIn("page-sources", response.url)
        plan = request.session[SESSION_KEY_PLAN]
        self.assertEqual(plan.context[PLAN_CONTEXT_PENDING_USER].pk, self.user_b.pk)
        self.assertEqual(
            plan.context[PLAN_CONTEXT_SOURCE_REAUTHENTICATION],
            str(self.auth_flow.pk),
        )
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_pending_username_link_authenticates_without_saving_connection(self):
        """A username link logs the matched account in and does not save the row yet."""
        self.source.user_matching_mode = SourceUserMatchingModes.USERNAME_LINK
        self.source.save()
        request = self._request(self.user_a, pending=True)
        manager = self._manager(request, {"preferred_username": self.user_b.username})
        seen: list[tuple[str, int | None]] = []

        class Hook:
            def oauth_pre_auth(self, _manager, connection):
                seen.append(("auth", connection.user_id))

            def oauth_pre_existing_link(self, _manager, connection):
                seen.append(("link", connection.user_id))
                return HttpResponse("linked")

            def oauth_pre_enroll(self, _manager, connection):
                seen.append(("enroll", connection.user_id))
                return HttpResponse("enrolled")

        manager.source_type = Hook
        response = manager.get_flow(access_token="access-token", expires=120)

        self.assertEqual(seen, [("auth", self.user_b.pk)])
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.auth_flow.slug, response.url)
        self.assertNotIn("page-sources", response.url)
        plan = request.session[SESSION_KEY_PLAN]
        self.assertEqual(plan.context[PLAN_CONTEXT_PENDING_USER].pk, self.user_b.pk)
        self.assertEqual(
            plan.context[PLAN_CONTEXT_SOURCE_REAUTHENTICATION],
            str(self.auth_flow.pk),
        )
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_pending_without_connection_is_refused(self):
        """No linked account is an error, not a link to the session user or an enrollment."""
        request = self._request(self.user_a, pending=True)
        manager = self._manager(request)
        seen: list[str] = []

        class Hook:
            def oauth_pre_enroll(self, _manager, connection):
                seen.append("enroll")
                return HttpResponse("enrolled")

            def oauth_pre_existing_link(self, _manager, connection):
                seen.append("link")
                return HttpResponse("linked")

            def oauth_pre_auth(self, _manager, connection):
                seen.append("auth")
                return HttpResponse("authed")

        manager.source_type = Hook
        before_users = User.objects.count()
        response = manager.get_flow(expires=120)

        self.assertIsInstance(response, AccessDeniedResponse)
        self.assertEqual(
            response.error_message,
            f"Re-authentication requires an existing account connected to {self.source.name}.",
        )
        self.assertEqual(seen, [])
        self.assertEqual(User.objects.count(), before_users)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())
        self.assertNotIn(SESSION_KEY_PLAN, request.session)
        self.assertNotIn(self.enroll_flow.slug, getattr(response, "url", "") or "")

    def test_without_pending_links_session_user(self):
        """With no marker, the callback still links the identity to the session user."""
        request = self._request(self.user_a, pending=False)
        response = self._manager(request).get_flow()

        self.assertEqual(response.status_code, 302)
        self.assertIn("#/settings;page-sources", response.url)
        connection = UserOAuthSourceConnection.objects.get(
            source=self.source, identifier=IDENTIFIER
        )
        self.assertEqual(connection.user_id, self.user_a.pk)

    def test_stale_marker_refuses_new_identifier(self):
        """An expired or mismatched marker is an error and does not link the session user."""
        cases = ("expired", "mismatched")
        for name in cases:
            with self.subTest(name=name):
                request = self._request(self.user_a, pending=True)
                marker = dict(request.session[SESSION_KEY_SOURCE_REAUTHENTICATION])
                if name == "expired":
                    marker["expires"] = time() - 1
                else:
                    marker["login_uid"] = "other-login"
                request.session[SESSION_KEY_SOURCE_REAUTHENTICATION] = marker
                manager = self._manager(request)
                seen: list[str] = []

                class Hook:
                    def oauth_pre_enroll(self, _manager, connection, seen=seen):
                        seen.append("enroll")
                        return HttpResponse("enrolled")

                    def oauth_pre_existing_link(self, _manager, connection, seen=seen):
                        seen.append("link")
                        return HttpResponse("linked")

                    def oauth_pre_auth(self, _manager, connection, seen=seen):
                        seen.append("auth")
                        return HttpResponse("authed")

                manager.source_type = Hook
                response = manager.get_flow(expires=120)

                self.assertIsInstance(response, AccessDeniedResponse)
                self.assertEqual(
                    response.error_message,
                    "Re-authentication expired. Please start the operation again.",
                )
                self.assertEqual(seen, [])
                self.assertFalse(
                    UserOAuthSourceConnection.objects.filter(source=self.source).exists()
                )
                self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)
                self.assertNotIn(SESSION_KEY_PLAN, request.session)

    def test_without_pending_existing_connection_is_not_applicable(self):
        """require_unauthenticated still blocks a normal logged-in source authentication."""
        UserOAuthSourceConnection.objects.create(
            user=self.user_b,
            source=self.source,
            identifier=IDENTIFIER,
        )
        request = self._request(self.user_a, pending=False)
        response = self._manager(request).get_flow(expires=120)

        self.assertIsInstance(response, AccessDeniedResponse)
        self.assertIn("Flow does not apply", str(response.error_message))
        self.assertNotIn(SESSION_KEY_PLAN, request.session)

    def test_pending_auth_runs_oauth_pre_auth(self):
        """AUTH during re-auth still enters the source pre-auth hook."""
        UserOAuthSourceConnection.objects.create(
            user=self.user_b,
            source=self.source,
            identifier=IDENTIFIER,
        )
        request = self._request(self.user_a, pending=True)
        manager = self._manager(request)
        seen: dict[str, int | None] = {}

        class Hook:
            def oauth_pre_auth(self, _manager, connection):
                seen["user_id"] = connection.user_id
                return HttpResponse("checked")

        manager.source_type = Hook
        response = manager.get_flow(expires=120)

        self.assertEqual(seen["user_id"], self.user_b.pk)
        self.assertEqual(response.content, b"checked")

    def test_pending_email_deny_does_not_link_session_user(self):
        """Email deny during re-auth does not attach the identity to the session user."""
        self.source.user_matching_mode = SourceUserMatchingModes.EMAIL_DENY
        self.source.save()
        self.user_b.email = "shared@example.com"
        self.user_b.save()
        request = self._request(self.user_a, pending=True)

        action, connection = self._manager(request, {"email": "shared@example.com"}).get_action()

        self.assertEqual(action, Action.DENY)
        self.assertIsNone(connection)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_pending_flow_reads_reauthentication_state_once(self):
        """The pending flag and the stale check share one read of the marker."""
        UserOAuthSourceConnection.objects.create(
            user=self.user_b,
            source=self.source,
            identifier=IDENTIFIER,
        )
        request = self._request(self.user_a, pending=True)
        manager = self._manager(request)
        with patch(
            "authentik.sources.oauth.views.callback.source_reauthentication_state",
            wraps=source_reauthentication_state,
        ) as state:
            response = manager.get_flow(expires=120)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(state.call_count, 1)
