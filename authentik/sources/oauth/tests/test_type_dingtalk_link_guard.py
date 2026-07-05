"""DingTalk source-link guard tests."""

from django.test import TestCase
from django.urls import reverse
from requests_mock import Mocker

from authentik.core.models import User, UserTypes
from authentik.core.tests.utils import create_test_admin_user, create_test_flow, create_test_user
from authentik.flows.models import FlowStageBinding
from authentik.flows.views.executor import SESSION_KEY_PLAN
from authentik.policies.expression.models import ExpressionPolicy
from authentik.policies.models import PolicyBinding
from authentik.sources.oauth.api.dingtalk_allowlist import render_dingtalk_allowlist_policy
from authentik.sources.oauth.models import OAuthSource, UserOAuthSourceConnection
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_ACCESS_TOKEN_URL,
    DINGTALK_ALLOWLIST_MARKER,
    DINGTALK_ALLOWLIST_PLAN_CONTEXT,
    DINGTALK_ALLOWLIST_SESSION_KEY,
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_GET_BY_UNION_ID_URL,
    DINGTALK_PROFILE_URL,
    DINGTALK_USER_DETAIL_URL,
)
from authentik.stages.dummy.models import DummyStage
from authentik.tenants.utils import get_current_tenant


class TestDingTalkSourceLinkGuard(TestCase):
    """Test authenticated DingTalk source linking against allowlist policy."""

    def setUp(self):
        tenant = get_current_tenant()
        tenant.avatars = "none"
        tenant.save()
        self.user = create_test_admin_user()
        self.client.force_login(self.user)
        self.source = OAuthSource.objects.create(
            name="DingTalk Test",
            slug="dingtalk-test",
            provider_type="dingtalk",
            consumer_key="FAKE_CLIENT_ID",
            consumer_secret="FAKE_CLIENT_SECRET",
            authentication_flow=create_test_flow(),
            enrollment_flow=create_test_flow(),
        )

    def bind_allowlist(self, companies, target=None):
        """Bind a managed DingTalk allowlist expression to the source."""
        policy = ExpressionPolicy.objects.create(
            name="managed-dingtalk",
            expression=render_dingtalk_allowlist_policy({"companies": companies}),
        )
        PolicyBinding.objects.create(
            target=target or self.source,
            policy=policy,
            order=0,
            enabled=True,
        )

    def start_login(self):
        """Start OAuth login and return state."""
        response = self.client.get(
            reverse(
                "authentik_sources_oauth:oauth-client-login",
                kwargs={"source_slug": self.source.slug},
            )
        )
        self.assertEqual(response.status_code, 302)
        return self.client.session["oauth-client-DingTalk Test-request-state"]

    def mock_dingtalk_callback(self, mocker, *, corp_id="CORP_FAKE", depts=None):
        """Mock DingTalk token/profile/directory endpoints."""
        mocker.post(
            DINGTALK_ACCESS_TOKEN_URL,
            json={
                "accessToken": "FAKE_USER_TOKEN",
                "refreshToken": "FAKE_REFRESH_TOKEN",
                "expireIn": 7200,
                "corpId": corp_id,
            },
        )
        mocker.get(
            DINGTALK_PROFILE_URL,
            json={
                "unionId": "UNION_FAKE",
                "openId": "OPEN_FAKE",
                "corpId": corp_id,
                "nick": "Ada",
            },
        )
        mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "FAKE_APP_TOKEN"})
        mocker.post(
            DINGTALK_GET_BY_UNION_ID_URL,
            json={"errcode": 0, "result": {"userid": "USER_FAKE"}},
        )
        mocker.post(
            DINGTALK_USER_DETAIL_URL,
            json={
                "errcode": 0,
                "result": {
                    "userid": "USER_FAKE",
                    "name": "Ada Lovelace",
                    "dept_id_list": [] if depts is None else depts,
                },
            },
        )

    def callback(self, state):
        """Complete OAuth callback."""
        return self.client.get(
            reverse(
                "authentik_sources_oauth:oauth-client-callback",
                kwargs={"source_slug": self.source.slug},
            ),
            {"authCode": "AUTH_CODE", "state": state},
        )

    def test_authenticated_link_allows_matching_department(self):
        """Allowed corp and department creates a user source connection."""
        self.bind_allowlist([{"corp_id": "CORP_FAKE", "dept_ids": [10]}])
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[10])
            response = self.callback(state)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            UserOAuthSourceConnection.objects.filter(
                source=self.source, user=self.user, identifier="UNION_FAKE"
            ).exists()
        )

    def test_authenticated_link_denies_rejected_corp_before_save(self):
        """Rejected corp does not create a user source connection."""
        self.bind_allowlist([{"corp_id": "CORP_ALLOWED", "allow_all": True}])
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, corp_id="CORP_DENIED", depts=[10])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())
        self.assertNotIn(DINGTALK_ALLOWLIST_SESSION_KEY, self.client.session)

    def test_authenticated_link_denies_rejected_department_before_save(self):
        """Rejected department does not create a user source connection."""
        self.bind_allowlist([{"corp_id": "CORP_FAKE", "dept_ids": [10]}])
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[30])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_user_settings_lists_dingtalk_source_when_source_allowlist_has_no_oauth_userinfo(self):
        """User settings should not hide DingTalk when OAuth-only allowlist data is absent."""
        self.bind_allowlist([{"corp_id": "CORP_FAKE", "allow_all": True}])

        response = self.client.get(reverse("authentik_api:source-user-settings"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.source.slug, [item["object_uid"] for item in response.json()])

    def test_authenticated_link_denies_managed_marker_allowlist_before_save(self):
        """Source-link guard recognizes the managed marker before linking."""
        self.bind_allowlist(
            [{"corp_id": "CORP_FAKE", "allow_all": False, "dept_ids": ["10"], "label": ""}]
        )
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[30])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_authenticated_link_denies_when_allowlist_bound_to_authentication_flow(self):
        """Source-link guard finds the UI-managed policy on the authentication flow."""
        self.bind_allowlist(
            [{"corp_id": "CORP_FAKE", "dept_ids": [10]}],
            target=self.source.authentication_flow,
        )
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[30])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_authenticated_link_denies_when_allowlist_bound_to_enrollment_flow(self):
        """Source-link guard finds the UI-managed policy on the enrollment flow."""
        self.bind_allowlist(
            [{"corp_id": "CORP_FAKE", "dept_ids": [10]}],
            target=self.source.enrollment_flow,
        )
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[30])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_authenticated_link_denies_when_allowlist_bound_to_auth_flow_stage_binding(self):
        """Source-link guard finds a managed policy bound to an auth flow-stage binding."""
        stage_binding = FlowStageBinding.objects.create(
            target=self.source.authentication_flow,
            stage=DummyStage.objects.create(name="dingtalk-allowlist-stage"),
            order=0,
        )
        self.bind_allowlist(
            [{"corp_id": "CORP_FAKE", "dept_ids": [10]}],
            target=stage_binding,
        )
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[30])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_unauthenticated_auth_flow_denies_existing_user_before_connection_write(self):
        """Normal auth flow policy denial does not write updated connection data."""
        existing_user = create_test_user("dingtalk-existing")
        UserOAuthSourceConnection.objects.create(
            source=self.source,
            user=existing_user,
            identifier="UNION_FAKE",
            access_token="OLD_ACCESS_TOKEN",
            refresh_token="OLD_REFRESH_TOKEN",
        )
        self.bind_allowlist(
            [{"corp_id": "CORP_FAKE", "dept_ids": [10]}],
            target=self.source.authentication_flow,
        )
        self.client.logout()
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[30])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        connection = UserOAuthSourceConnection.objects.get(
            source=self.source, identifier="UNION_FAKE"
        )
        self.assertEqual(connection.access_token, "OLD_ACCESS_TOKEN")
        self.assertEqual(connection.refresh_token, "OLD_REFRESH_TOKEN")

    def test_unauthenticated_auth_flow_sets_allowlist_plan_marker_when_allowed(self):
        """Allowed DingTalk auth carries allowlist evidence into the login flow plan."""
        existing_user = create_test_user("dingtalk-existing")
        UserOAuthSourceConnection.objects.create(
            source=self.source,
            user=existing_user,
            identifier="UNION_FAKE",
            access_token="OLD_ACCESS_TOKEN",
            refresh_token="OLD_REFRESH_TOKEN",
        )
        self.bind_allowlist(
            [{"corp_id": "CORP_FAKE", "dept_ids": [10]}],
            target=self.source.authentication_flow,
        )
        self.client.logout()
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[10])
            response = self.callback(state)

        self.assertEqual(response.status_code, 302)
        plan = self.client.session[SESSION_KEY_PLAN]
        marker = plan.context[DINGTALK_ALLOWLIST_PLAN_CONTEXT]
        self.assertEqual(marker["source_slug"], self.source.slug)
        self.assertEqual(marker["corp_id"], "CORP_FAKE")
        self.assertEqual(marker["dept_ids"], ["10"])

    def test_unauthenticated_auth_flow_promotes_existing_external_user_to_internal(self):
        """Allowed DingTalk auth promotes previously-enrolled external users to internal."""
        existing_user = create_test_user("dingtalk-existing")
        existing_user.type = UserTypes.EXTERNAL
        existing_user.save(update_fields=["type"])
        UserOAuthSourceConnection.objects.create(
            source=self.source,
            user=existing_user,
            identifier="UNION_FAKE",
            access_token="OLD_ACCESS_TOKEN",
            refresh_token="OLD_REFRESH_TOKEN",
        )
        self.bind_allowlist(
            [{"corp_id": "CORP_FAKE", "dept_ids": [10]}],
            target=self.source.authentication_flow,
        )
        self.client.logout()
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[10])
            response = self.callback(state)

        self.assertEqual(response.status_code, 302)
        existing_user.refresh_from_db()
        self.assertEqual(existing_user.type, UserTypes.INTERNAL)

    def test_unauthenticated_auth_flow_keeps_denied_external_user_type(self):
        """Denied DingTalk auth does not change the linked user's type."""
        existing_user = create_test_user("dingtalk-existing")
        existing_user.type = UserTypes.EXTERNAL
        existing_user.save(update_fields=["type"])
        UserOAuthSourceConnection.objects.create(
            source=self.source,
            user=existing_user,
            identifier="UNION_FAKE",
            access_token="OLD_ACCESS_TOKEN",
            refresh_token="OLD_REFRESH_TOKEN",
        )
        self.bind_allowlist(
            [{"corp_id": "CORP_FAKE", "dept_ids": [10]}],
            target=self.source.authentication_flow,
        )
        self.client.logout()
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[30])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        existing_user.refresh_from_db()
        self.assertEqual(existing_user.type, UserTypes.EXTERNAL)

    def test_unauthenticated_auth_flow_denies_source_bound_allowlist_before_connection_write(self):
        """Source-bound DingTalk allowlist denies existing-user login before token update."""
        existing_user = create_test_user("dingtalk-existing")
        UserOAuthSourceConnection.objects.create(
            source=self.source,
            user=existing_user,
            identifier="UNION_FAKE",
            access_token="OLD_ACCESS_TOKEN",
            refresh_token="OLD_REFRESH_TOKEN",
        )
        self.bind_allowlist([{"corp_id": "CORP_FAKE", "dept_ids": [10]}])
        self.client.logout()
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[30])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        connection = UserOAuthSourceConnection.objects.get(
            source=self.source, identifier="UNION_FAKE"
        )
        self.assertEqual(connection.access_token, "OLD_ACCESS_TOKEN")
        self.assertEqual(connection.refresh_token, "OLD_REFRESH_TOKEN")

    def test_unauthenticated_enrollment_flow_denies_new_user_before_create(self):
        """Normal enrollment flow policy denial does not create a user or connection."""
        self.bind_allowlist(
            [{"corp_id": "CORP_FAKE", "dept_ids": [10]}],
            target=self.source.enrollment_flow,
        )
        self.client.logout()
        user_count = User.objects.count()
        connection_count = UserOAuthSourceConnection.objects.count()
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[30])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), user_count)
        self.assertEqual(UserOAuthSourceConnection.objects.count(), connection_count)

    def test_enrollment_without_userid_fails_closed(self):
        """B2: enrollment is denied when the DingTalk userid (username source) is unavailable."""
        self.bind_allowlist([{"corp_id": "CORP_FAKE", "allow_all": True}])
        self.client.logout()
        user_count = User.objects.count()
        state = self.start_login()

        with Mocker() as mocker:
            mocker.post(
                DINGTALK_ACCESS_TOKEN_URL,
                json={"accessToken": "FAKE_USER_TOKEN", "corpId": "CORP_FAKE"},
            )
            mocker.get(
                DINGTALK_PROFILE_URL,
                json={"unionId": "UNION_FAKE", "corpId": "CORP_FAKE", "nick": "Ada"},
            )
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "FAKE_APP_TOKEN"})
            # Enhancement returns no userid, so the username cannot be resolved.
            mocker.post(DINGTALK_GET_BY_UNION_ID_URL, json={"errcode": 0, "result": {}})
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), user_count)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_unparseable_managed_allowlist_fails_closed(self):
        """B5: a managed allowlist whose config cannot be parsed denies login, not fail-open."""
        policy = ExpressionPolicy.objects.create(
            name="corrupt-dingtalk",
            expression=f"{DINGTALK_ALLOWLIST_MARKER}\n# config: {{not-valid-json\nreturn True",
        )
        PolicyBinding.objects.create(target=self.source, policy=policy, order=0, enabled=True)
        state = self.start_login()

        with Mocker() as mocker:
            self.mock_dingtalk_callback(mocker, depts=[10])
            response = self.callback(state)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())
