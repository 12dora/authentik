"""login tests"""

from time import sleep
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.http import HttpRequest
from django.http.response import HttpResponse
from django.urls import reverse
from django.utils.timezone import now

from authentik.blueprints.tests import apply_blueprint
from authentik.core import user_switching
from authentik.core.models import AuthenticatedSession, Session, User, UserSwitchingSession
from authentik.core.sessions import SessionStore
from authentik.core.sources.reauthentication import (
    SESSION_KEY_SOURCE_REAUTHENTICATION,
    mark_source_reauthentication,
)
from authentik.core.tests.utils import create_test_flow, create_test_user
from authentik.events.models import Event, EventAction
from authentik.events.signals import get_login_event
from authentik.events.utils import get_user
from authentik.flows.markers import StageMarker
from authentik.flows.models import FlowDesignation, FlowStageBinding
from authentik.flows.planner import (
    PLAN_CONTEXT_PENDING_USER,
    PLAN_CONTEXT_USER_SWITCH_ADD_USER,
    FlowPlan,
)
from authentik.flows.tests import FlowTestCase
from authentik.flows.tests.test_executor import TO_STAGE_RESPONSE_MOCK
from authentik.flows.views.executor import NEXT_ARG_NAME, SESSION_KEY_PLAN
from authentik.lib.generators import generate_id
from authentik.lib.utils.time import timedelta_from_string
from authentik.providers.oauth2.views.authorize import (
    SESSION_KEY_LAST_LOGIN_UID as OAUTH2_LAST_LOGIN_UID,
)
from authentik.providers.saml.views.sso import (
    SESSION_KEY_LAST_LOGIN_UID as SAML_LAST_LOGIN_UID,
)
from authentik.root.middleware import ClientIPMiddleware
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_ALLOWLIST_PLAN_CONTEXT,
    DINGTALK_ALLOWLIST_SESSION_KEY,
)
from authentik.stages.user_login.middleware import (
    SESSION_KEY_BINDING_NET,
    BoundSessionMiddleware,
    SessionBindingBroken,
    logout_extra,
)
from authentik.stages.user_login.models import GeoIPBinding, NetworkBinding, UserLoginStage
from authentik.stages.user_login.signals import user_login_session_finalized
from authentik.stages.user_login.stage import UserLoginStageView


class TestUserLoginStage(FlowTestCase):
    """Login tests"""

    def setUp(self):
        super().setUp()
        self.user = create_test_user()

        self.flow = create_test_flow(FlowDesignation.AUTHENTICATION)
        self.stage = UserLoginStage.objects.create(name="login")
        self.binding = FlowStageBinding.objects.create(target=self.flow, stage=self.stage, order=2)

    def test_valid_get(self):
        """Test with a valid pending user and backend"""
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()

        response = self.client.get(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertStageRedirects(response, reverse("authentik_core:root-redirect"))

    def test_stale_user_switching_cookie_is_replaced(self):
        """A signed cookie without a switching session does not break login."""
        stale_token = generate_id(user_switching.TOKEN_LENGTH)
        self.client.cookies[settings.USER_SWITCHING_COOKIE_NAME] = user_switching.encode_cookie(
            stale_token
        )
        plan = FlowPlan(
            flow_pk=self.flow.pk.hex,
            bindings=[self.binding],
            markers=[StageMarker()],
        )
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()

        response = self.client.get(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertStageRedirects(response, reverse("authentik_core:root-redirect"))
        switching_session = UserSwitchingSession.objects.get(authenticated_sessions__user=self.user)
        self.assertNotEqual(switching_session.token, stale_token)
        self.assertEqual(
            user_switching.decode_cookie(
                self.client.cookies[settings.USER_SWITCHING_COOKIE_NAME].value
            ),
            switching_session.token,
        )

    def test_valid_post(self):
        """Test with a valid pending user and backend"""
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()

        response = self.client.post(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertStageRedirects(response, reverse("authentik_core:root-redirect"))

    def test_dingtalk_allowlist_marker_is_persisted_after_login(self):
        """DingTalk allowlist evidence is written to the post-login session."""
        marker = {
            "source_slug": "dingtalk",
            "corp_id": "CORP_ALLOWED",
            "dept_ids": ["10"],
            "user_pk": self.user.pk,
        }
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        plan.context[DINGTALK_ALLOWLIST_PLAN_CONTEXT] = marker
        request = HttpRequest()
        request.session = self.client.session
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.COOKIES = {}
        executor = SimpleNamespace(
            plan=plan,
            current_stage=self.stage,
            flow=self.flow,
            stage_ok=Mock(return_value=HttpResponse()),
        )
        view = UserLoginStageView(executor)
        view.request = request

        response = view.do_login(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.session[DINGTALK_ALLOWLIST_SESSION_KEY], marker)

    def _logged_in_reauth_session(self, *, pending: bool = True, flush: bool = False):
        """Session for self.user with provider last-login ids and, optionally, the marker."""
        self.client.force_login(self.user)
        session = self.client.session
        session[OAUTH2_LAST_LOGIN_UID] = "oauth-login-uid-a"
        session[SAML_LAST_LOGIN_UID] = None
        session["unrelated"] = "drop-on-flush"
        # authentik's SessionStore does not persist Django's auth user id, so a reloaded
        # session makes login() cycle the key. Set it so login() takes the flush path,
        # the worst case for keeping the provider ids (opt-in via ``flush``).
        if flush:
            session[SESSION_KEY] = str(self.user.pk)
        if pending:
            request = HttpRequest()
            request.session = session
            mark_source_reauthentication(request)
        return session

    def _do_login_as(self, session, pending_user: User, *, user_switch: bool = False):
        """Log pending_user in on session. Returns the response and the request."""
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = pending_user
        if user_switch:
            plan.context[PLAN_CONTEXT_USER_SWITCH_ADD_USER] = True
        session[SESSION_KEY_PLAN] = plan
        request = HttpRequest()
        request.session = session
        request.user = self.user
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.COOKIES = {}
        executor = SimpleNamespace(
            plan=plan,
            current_stage=self.stage,
            flow=self.flow,
            stage_ok=Mock(return_value=HttpResponse()),
        )
        view = UserLoginStageView(executor)
        view.request = request
        return view.do_login(request), request

    def test_cross_user_login_keeps_provider_last_login_uid(self):
        """A pending re-auth keeps provider last_login_uid values and clears the marker."""
        session = self._logged_in_reauth_session(flush=True)
        other = create_test_user()

        response, request = self._do_login_as(session, other)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("unrelated", request.session)
        self.assertIn(SAML_LAST_LOGIN_UID, request.session)
        self.assertEqual(request.session[OAUTH2_LAST_LOGIN_UID], "oauth-login-uid-a")
        self.assertIsNone(request.session[SAML_LAST_LOGIN_UID])
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)
        login_event = get_login_event(request)
        self.assertIsNotNone(login_event)
        self.assertNotEqual(str(login_event.pk), request.session[OAUTH2_LAST_LOGIN_UID])
        request.session.save()
        stored = SessionStore(request.session.session_key)
        self.assertEqual(stored[OAUTH2_LAST_LOGIN_UID], "oauth-login-uid-a")
        self.assertIsNone(stored[SAML_LAST_LOGIN_UID])
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, stored)

    def test_cross_user_login_without_marker_drops_provider_last_login_uid(self):
        """A different user without a pending marker does not keep the provider ids."""
        session = self._logged_in_reauth_session(pending=False, flush=True)
        other = create_test_user()

        response, request = self._do_login_as(session, other)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("unrelated", request.session)
        self.assertNotIn(OAUTH2_LAST_LOGIN_UID, request.session)
        self.assertNotIn(SAML_LAST_LOGIN_UID, request.session)
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)
        request.session.save()
        stored = SessionStore(request.session.session_key)
        self.assertNotIn(OAUTH2_LAST_LOGIN_UID, stored)
        self.assertNotIn(SAML_LAST_LOGIN_UID, stored)
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, stored)

    def test_same_user_login_clears_source_reauthentication_marker(self):
        """Logging in again as the same user drops the marker and keeps session keys."""
        session = self._logged_in_reauth_session()

        response, request = self._do_login_as(session, self.user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.session["unrelated"], "drop-on-flush")
        self.assertIn(SAML_LAST_LOGIN_UID, request.session)
        self.assertEqual(request.session[OAUTH2_LAST_LOGIN_UID], "oauth-login-uid-a")
        self.assertIsNone(request.session[SAML_LAST_LOGIN_UID])
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)
        request.session.save()
        stored = SessionStore(request.session.session_key)
        self.assertEqual(stored["unrelated"], "drop-on-flush")
        self.assertEqual(stored[OAUTH2_LAST_LOGIN_UID], "oauth-login-uid-a")
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, stored)

    def test_user_switch_does_not_copy_provider_last_login_uid(self):
        """User-switch does not copy provider ids, and drops the marker on both sessions."""
        session = self._logged_in_reauth_session()
        old_key = session.session_key
        other = create_test_user()

        response, request = self._do_login_as(session, other, user_switch=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(request.session.session_key, old_key)
        self.assertNotIn("unrelated", request.session)
        self.assertNotIn(OAUTH2_LAST_LOGIN_UID, request.session)
        self.assertNotIn(SAML_LAST_LOGIN_UID, request.session)
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, request.session)
        request.session.save()
        stored = SessionStore(request.session.session_key)
        self.assertNotIn(OAUTH2_LAST_LOGIN_UID, stored)
        self.assertNotIn(SAML_LAST_LOGIN_UID, stored)
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, stored)
        # The previous session stays readable even after it stops being current.
        raw = Session.objects.get(session_key=old_key).session_data
        if isinstance(raw, memoryview):
            raw = raw.tobytes()
        previous = SessionStore().decode(raw)
        self.assertEqual(previous[OAUTH2_LAST_LOGIN_UID], "oauth-login-uid-a")
        self.assertEqual(previous["unrelated"], "drop-on-flush")
        self.assertNotIn(SESSION_KEY_SOURCE_REAUTHENTICATION, previous)

    def test_login_session_finalized_receiver_exception_does_not_abort_login(self):
        """Post-login extension receivers are best-effort and cannot turn login into a 500."""

        def receiver(**_):
            raise RuntimeError("receiver failed")

        user_login_session_finalized.connect(
            receiver,
            dispatch_uid="authentik_test_login_session_finalized_failure",
        )
        self.addCleanup(
            user_login_session_finalized.disconnect,
            dispatch_uid="authentik_test_login_session_finalized_failure",
        )
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        request = HttpRequest()
        request.session = self.client.session
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.COOKIES = {}
        executor = SimpleNamespace(
            plan=plan,
            current_stage=self.stage,
            flow=self.flow,
            stage_ok=Mock(return_value=HttpResponse()),
        )
        view = UserLoginStageView(executor)
        view.request = request

        response = view.do_login(request)

        self.assertEqual(response.status_code, 200)
        view.executor.stage_ok.assert_called_once()

    def test_session_fixation_key_rotated_on_login(self):
        """Security regression (CWE-384): the session key must rotate on login
        so a pre-login session identifier known to an attacker can't be reused.
        Django's ``login()`` provides this via ``cycle_key()``; pinned here
        end-to-end through the flow executor and custom session backend."""
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()
        pre_login_key = session.session_key
        self.assertIsNotNone(pre_login_key)
        # Unauthenticated session persisted before login...
        self.assertTrue(Session.objects.filter(session_key=pre_login_key).exists())

        response = self.client.post(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertStageRedirects(response, reverse("authentik_core:root-redirect"))

        post_login_key = self.client.session.session_key
        # ...the identifier must change on authentication...
        self.assertIsNotNone(post_login_key)
        self.assertNotEqual(pre_login_key, post_login_key)
        # ...the pre-login key must no longer exist...
        self.assertFalse(Session.objects.filter(session_key=pre_login_key).exists())
        self.assertFalse(
            AuthenticatedSession.objects.filter(session__session_key=pre_login_key).exists()
        )
        # ...and only the rotated key may be bound to the authenticated user.
        self.assertTrue(
            AuthenticatedSession.objects.filter(
                session__session_key=post_login_key, user=self.user
            ).exists()
        )

    def test_terminate_other_sessions(self):
        """Test terminate_other_sessions"""
        self.stage.terminate_other_sessions = True
        self.stage.save()
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()

        key = generate_id()
        AuthenticatedSession.objects.create(
            session=Session.objects.create(
                session_key=key,
                last_ip=ClientIPMiddleware.default_ip,
            ),
            user=self.user,
        )

        response = self.client.post(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertStageRedirects(response, reverse("authentik_core:root-redirect"))
        self.assertFalse(AuthenticatedSession.objects.filter(session__session_key=key))
        self.assertFalse(Session.objects.filter(session_key=key).exists())

    def test_second_login_replaces_existing_session(self):
        """Test ordinary cross-user login keeps Django's session replacement behavior."""
        other_user = create_test_user()
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()
        response = self.client.get(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )
        self.assertEqual(response.status_code, 200)
        first_session_key = self.client.session.session_key

        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = other_user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()
        response = self.client.get(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )

        self.assertEqual(response.status_code, 200)
        second_session_key = self.client.session.session_key
        self.assertNotEqual(first_session_key, second_session_key)
        second = AuthenticatedSession.objects.get(session__session_key=second_session_key)
        self.assertEqual(second.user, other_user)
        self.assertTrue(second.is_current)
        self.assertFalse(
            AuthenticatedSession.objects.filter(session__session_key=first_session_key).exists()
        )
        self.assertFalse(Session.objects.filter(session_key=first_session_key).exists())

    def test_relogin_same_user_keeps_single_session(self):
        """Test re-logging in as the same user doesn't accumulate sessions"""
        for _ in range(2):
            plan = FlowPlan(
                flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()]
            )
            plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
            session = self.client.session
            session[SESSION_KEY_PLAN] = plan
            session.save()
            response = self.client.get(
                reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(AuthenticatedSession.objects.filter(user=self.user).count(), 1)
        self.assertIsNotNone(
            AuthenticatedSession.objects.get(user=self.user).user_switching_session_id
        )

    def test_expiry(self):
        """Test with expiry"""
        self.stage.session_duration = "seconds=2"
        self.stage.save()
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()

        before_request = now()
        response = self.client.get(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertStageRedirects(response, reverse("authentik_core:root-redirect"))
        self.assertNotEqual(list(self.client.session.keys()), [])
        session_key = self.client.session.session_key
        session = Session.objects.filter(session_key=session_key).first()
        self.assertAlmostEqual(
            session.expires.timestamp() - before_request.timestamp(),
            timedelta_from_string(self.stage.session_duration).total_seconds(),
            delta=1,
        )
        sleep(3)
        self.client.session.clear_expired()
        self.assertEqual(list(self.client.session.keys()), [])

    def test_expiry_remember(self):
        """Test with expiry"""
        self.stage.session_duration = "seconds=2"
        self.stage.remember_me_offset = "seconds=2"
        self.stage.save()
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()

        response = self.client.get(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug}),
        )
        self.assertStageResponse(response, component="ak-stage-user-login")

        response = self.client.post(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug}),
            data={"remember_me": True},
        )
        _now = now().timestamp()
        self.assertEqual(response.status_code, 200)
        self.assertStageRedirects(response, reverse("authentik_core:root-redirect"))
        self.assertNotEqual(list(self.client.session.keys()), [])
        session_key = self.client.session.session_key
        session = Session.objects.filter(session_key=session_key).first()
        self.assertAlmostEqual(
            session.expires.timestamp() - _now,
            timedelta_from_string(self.stage.session_duration).total_seconds()
            + timedelta_from_string(self.stage.remember_me_offset).total_seconds(),
            delta=1,
        )
        sleep(5)
        self.client.session.clear_expired()
        self.assertEqual(list(self.client.session.keys()), [])

    @patch(
        "authentik.flows.views.executor.to_stage_response",
        TO_STAGE_RESPONSE_MOCK,
    )
    def test_without_user(self):
        """Test a plan without any pending user, resulting in a denied"""
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()

        response = self.client.get(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )

        self.assertStageResponse(
            response,
            self.flow,
            component="ak-stage-access-denied",
        )

    @apply_blueprint("default/flow-default-user-settings-flow.yaml")
    def test_inactive_account(self):
        """Test with a valid pending user and backend"""
        self.user.is_active = False
        self.user.save()
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = self.user
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()

        response = self.client.get(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertStageResponse(
            response, self.flow, component="ak-stage-access-denied", error_message="Unknown error"
        )

        # Check that API requests get rejected
        response = self.client.get(reverse("authentik_api:application-list"))
        self.assertEqual(response.status_code, 403)

        # Check that flow requests requiring a user also get rejected
        response = self.client.get(
            reverse(
                "authentik_api:flow-executor",
                kwargs={"flow_slug": "default-user-settings-flow"},
            )
        )
        self.assertStageResponse(
            response,
            self.flow,
            component="ak-stage-access-denied",
            error_message="Flow does not apply to current user.",
        )

    def test_unsaved_pending_user(self):
        """Test that a pending user with no pk (unsaved) causes stage_invalid."""
        unsaved = User()
        plan = FlowPlan(flow_pk=self.flow.pk.hex, bindings=[self.binding], markers=[StageMarker()])
        plan.context[PLAN_CONTEXT_PENDING_USER] = unsaved
        session = self.client.session
        session[SESSION_KEY_PLAN] = plan
        session.save()

        response = self.client.get(
            reverse("authentik_api:flow-executor", kwargs={"flow_slug": self.flow.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertStageResponse(response, self.flow, component="ak-stage-access-denied")

    def test_binding_net_break_log(self):
        """Test logout_extra with exception"""
        # IPs from https://github.com/maxmind/MaxMind-DB/blob/main/source-data/GeoLite2-ASN-Test.json
        for args, expect in [
            [[NetworkBinding.BIND_ASN, "8.8.8.8", "8.8.8.8"], ["network.missing"]],
            [[NetworkBinding.BIND_ASN, "1.0.0.1", "1.128.0.1"], ["network.asn"]],
            [
                [NetworkBinding.BIND_ASN_NETWORK, "12.81.96.1", "12.81.128.1"],
                ["network.asn_network"],
            ],
            [[NetworkBinding.BIND_ASN_NETWORK_IP, "1.0.0.1", "1.0.0.2"], ["network.ip"]],
        ]:
            with self.subTest(args[0]):
                with self.assertRaises(SessionBindingBroken) as cm:
                    BoundSessionMiddleware.recheck_session_net(*args)
                self.assertEqual(cm.exception.reason, expect[0])
                # Ensure the request can be logged without throwing errors
                self.client.force_login(self.user)
                request = HttpRequest()
                request.session = self.client.session
                request.user = self.user
                logout_extra(request, cm.exception)

    def test_binding_geo_break_log(self):
        """Test logout_extra with exception"""
        # IPs from https://github.com/maxmind/MaxMind-DB/blob/main/source-data/GeoLite2-City-Test.json
        for args, expect in [
            [[GeoIPBinding.BIND_CONTINENT, "8.8.8.8", "8.8.8.8"], ["geoip.missing"]],
            [[GeoIPBinding.BIND_CONTINENT, "2.125.160.216", "67.43.156.1"], ["geoip.continent"]],
            [
                [GeoIPBinding.BIND_CONTINENT_COUNTRY, "81.2.69.142", "89.160.20.112"],
                ["geoip.country"],
            ],
            [
                [GeoIPBinding.BIND_CONTINENT_COUNTRY_CITY, "2.125.160.216", "81.2.69.142"],
                ["geoip.city"],
            ],
        ]:
            with self.subTest(args[0]):
                with self.assertRaises(SessionBindingBroken) as cm:
                    BoundSessionMiddleware.recheck_session_geo(*args)
                self.assertEqual(cm.exception.reason, expect[0])
                # Ensure the request can be logged without throwing errors
                self.client.force_login(self.user)
                request = HttpRequest()
                request.session = self.client.session
                request.user = self.user
                logout_extra(request, cm.exception)

    def test_session_binding_broken(self):
        """Test session binding"""
        Event.objects.all().delete()
        self.client.force_login(self.user)
        session = self.client.session
        session[Session.Keys.LAST_IP] = "192.0.2.1"
        session[SESSION_KEY_BINDING_NET] = NetworkBinding.BIND_ASN_NETWORK_IP
        session.save()

        res = self.client.get(reverse("authentik_api:user-me"))
        self.assertEqual(res.status_code, 302)
        self.assertEqual(
            res.url,
            reverse(
                "authentik_flows:default-authentication",
            )
            + f"?{NEXT_ARG_NAME}={reverse('authentik_api:user-me')}",
        )
        event = Event.objects.filter(action=EventAction.LOGOUT).first()
        self.assertEqual(event.user, get_user(self.user))
