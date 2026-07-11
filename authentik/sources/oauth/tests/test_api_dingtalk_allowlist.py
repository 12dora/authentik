"""DingTalk allowlist API tests."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.core import signing
from django.test import TestCase
from django.urls import reverse
from requests.exceptions import RequestException
from requests_mock import Mocker
from rest_framework.test import APITestCase

from authentik.core.tests.utils import create_test_admin_user, create_test_flow, create_test_user
from authentik.flows.views.executor import SESSION_KEY_PLAN
from authentik.policies.expression.models import ExpressionPolicy
from authentik.policies.models import PolicyBinding
from authentik.policies.types import PolicyRequest
from authentik.sources.oauth.api.dingtalk_allowlist import (
    DINGTALK_ALLOWLIST_MARKER,
    evaluate_dingtalk_allowlist,
    parse_dingtalk_allowlist_policy,
    render_dingtalk_allowlist_policy,
)
from authentik.sources.oauth.models import OAuthSource, UserOAuthSourceConnection
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_ACCESS_TOKEN_URL,
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_DEPARTMENT_LIST_URL,
    DINGTALK_ORG_AUTH_INFO_URL,
    DINGTALK_PROFILE_URL,
    _extract_dingtalk_corp_label,
    dingtalk_allowlist_config_version,
    fetch_dingtalk_departments,
)


class TestDingTalkAllowlistPolicyHelpers(TestCase):
    """Test DingTalk allowlist policy parser, renderer, and evaluator."""

    def test_parser_only_accepts_marked_policy(self):
        """Parser ignores unmarked expression bodies."""
        body = '# config: {"companies":[{"corp_id":"CORP_FAKE","allow_all":true}]}'

        self.assertIsNone(parse_dingtalk_allowlist_policy(body))

    def test_renderer_normalizes_config_and_expression_allows_matching_dept(self):
        """Renderer emits deterministic expression policy with normalized config."""
        body = render_dingtalk_allowlist_policy(
            {
                "companies": [
                    {
                        "corpId": "CORP_FAKE",
                        "label": "Fake Company",
                        "dept_ids": [20, "10"],
                    }
                ]
            }
        )

        self.assertIn(DINGTALK_ALLOWLIST_MARKER, body)
        self.assertIn(
            '# config: {"companies":[{"allow_all":false,"corp_id":"CORP_FAKE",'
            '"dept_ids":["10","20"],"label":"Fake Company"}]}',
            body,
        )
        parsed = parse_dingtalk_allowlist_policy(body)
        self.assertEqual(
            parsed,
            {
                "companies": [
                    {
                        "allow_all": False,
                        "corp_id": "CORP_FAKE",
                        "dept_ids": ["10", "20"],
                        "label": "Fake Company",
                    }
                ]
            },
        )

        policy = ExpressionPolicy.objects.create(name="dingtalk-policy", expression=body)
        request = PolicyRequest(create_test_admin_user())
        request.context["oauth_userinfo"] = {"corpId": "CORP_FAKE", "dept_id_list": [10]}

        self.assertTrue(policy.passes(request).passing)

    def test_rendered_expression_emits_translatable_english_deny_messages(self):
        """Rendered deny paths emit stable English gettext message IDs."""
        body = render_dingtalk_allowlist_policy(
            {"companies": [{"corp_id": "CORP_ALLOWED", "dept_ids": [10]}]}
        )
        policy = ExpressionPolicy.objects.create(name="dingtalk-policy-message", expression=body)
        request = PolicyRequest(create_test_admin_user())

        request.context["oauth_userinfo"] = {"corpId": "CORP_DENIED", "dept_id_list": [10]}
        denied_corp = policy.passes(request)

        self.assertFalse(denied_corp.passing)
        self.assertEqual(
            denied_corp.messages,
            ("DingTalk login failed: your company is not allowed. Contact your administrator.",),
        )

        request.context["oauth_userinfo"] = {"corpId": "CORP_ALLOWED", "dept_id_list": [30]}
        denied_dept = policy.passes(request)

        self.assertFalse(denied_dept.passing)
        self.assertEqual(
            denied_dept.messages,
            ("DingTalk login failed: your department is not allowed. Contact your administrator.",),
        )

    def test_authorization_version_ignores_display_label(self):
        """Renaming a company does not invalidate otherwise-identical sessions."""
        first = dingtalk_allowlist_config_version(
            {
                "companies": [
                    {
                        "corp_id": "CORP_ALLOWED",
                        "label": "First label",
                        "dept_ids": [10],
                    }
                ]
            }
        )
        renamed = dingtalk_allowlist_config_version(
            {
                "companies": [
                    {
                        "corp_id": "CORP_ALLOWED",
                        "label": "Renamed",
                        "dept_ids": [10],
                    }
                ]
            }
        )

        self.assertEqual(first, renamed)
        self.assertNotIn("label", first)

    def test_rendered_expression_fails_closed_for_dingtalk_login_without_corp(self):
        """B4: a DingTalk source login (userinfo present) missing the corp id is denied."""
        body = render_dingtalk_allowlist_policy(
            {"companies": [{"corp_id": "CORP_FAKE", "allow_all": True}]}
        )
        policy = ExpressionPolicy.objects.create(name="dingtalk-b4", expression=body)
        source = OAuthSource(name="dt", slug="dt", provider_type="dingtalk")
        request = PolicyRequest(create_test_admin_user())
        request.obj = source
        request.context["source"] = source
        request.context["oauth_userinfo"] = {"unionId": "UNION_FAKE", "nick": "Ada"}

        self.assertFalse(policy.passes(request).passing)

    def test_rendered_expression_allows_other_source_sharing_flow(self):
        """B4: a non-DingTalk source on a shared flow is not blocked by the allowlist."""
        body = render_dingtalk_allowlist_policy(
            {"companies": [{"corp_id": "CORP_FAKE", "allow_all": True}]}
        )
        policy = ExpressionPolicy.objects.create(name="dingtalk-b4-other", expression=body)
        source = OAuthSource(name="ga", slug="ga", provider_type="google")
        request = PolicyRequest(create_test_admin_user())
        request.obj = source
        request.context["source"] = source
        request.context["oauth_userinfo"] = {"sub": "abc"}

        self.assertTrue(policy.passes(request).passing)

    def test_evaluator_allow_all_and_fail_closed_cases(self):
        """Evaluator enforces corp and department restrictions fail-closed."""
        config = {
            "companies": [
                {"corp_id": "CORP_ALL", "allow_all": True},
                {"corp_id": "CORP_RESTRICTED", "dept_ids": ["10", "20"]},
            ]
        }

        self.assertTrue(
            evaluate_dingtalk_allowlist(config, {"corpId": "CORP_ALL", "dept_id_list": "bad"})
        )
        self.assertTrue(
            evaluate_dingtalk_allowlist(
                config, {"corp_id": "CORP_RESTRICTED", "dept_id_list": [20]}
            )
        )
        self.assertFalse(
            evaluate_dingtalk_allowlist(config, {"corpId": "CORP_DENIED", "dept_id_list": [20]})
        )
        self.assertFalse(
            evaluate_dingtalk_allowlist(config, {"corpId": "CORP_RESTRICTED", "dept_id_list": [30]})
        )
        self.assertFalse(evaluate_dingtalk_allowlist(config, {"dept_id_list": [10]}))
        self.assertFalse(evaluate_dingtalk_allowlist(config, {"corpId": "CORP_RESTRICTED"}))
        self.assertFalse(
            evaluate_dingtalk_allowlist(config, {"corpId": "CORP_RESTRICTED", "dept_id_list": "10"})
        )

    def test_parser_tolerates_legacy_department_and_name_keys(self):
        """Parser accepts legacy managed config while returning the canonical schema."""
        body = (
            f"{DINGTALK_ALLOWLIST_MARKER}\n"
            '# config: {"companies":[{"corp_id":"CORP_FAKE","name":"Legacy",'
            '"dept_id_list":[20,"10"],"allow_all":false}]}'
        )

        self.assertEqual(
            parse_dingtalk_allowlist_policy(body),
            {
                "companies": [
                    {
                        "allow_all": False,
                        "corp_id": "CORP_FAKE",
                        "dept_ids": ["10", "20"],
                        "label": "Legacy",
                    }
                ]
            },
        )

    def test_parser_accepts_managed_marker(self):
        """Parser accepts the single managed allowlist policy marker."""
        body = (
            "# authentik-managed-dingtalk-allowlist\n"
            '# config: {"companies":[{"corp_id":"CORP_FAKE","label":"Fake",'
            '"dept_ids":[20,"10"],"allow_all":false}]}'
        )

        self.assertEqual(
            parse_dingtalk_allowlist_policy(body),
            {
                "companies": [
                    {
                        "allow_all": False,
                        "corp_id": "CORP_FAKE",
                        "dept_ids": ["10", "20"],
                        "label": "Fake",
                    }
                ]
            },
        )

    def test_parser_accepts_managed_marker_and_empty_config_denies_all(self):
        """Parser recognizes the managed marker and keeps empty allowlists fail-closed."""
        body = '# authentik-managed-dingtalk-allowlist\n# config: {"companies":[]}'

        parsed = parse_dingtalk_allowlist_policy(body)

        self.assertEqual(parsed, {"companies": []})
        self.assertFalse(evaluate_dingtalk_allowlist(parsed, {"corpId": "CORP_FAKE"}))


class TestDingTalkAllowlistAPI(APITestCase):
    """Test DingTalk allowlist discovery API."""

    def setUp(self):
        self.user = create_test_admin_user()
        self.client.force_login(self.user)
        self.source = OAuthSource.objects.create(
            name="DingTalk Test",
            slug="dingtalk-test",
            provider_type="dingtalk",
            consumer_key="FAKE_CLIENT_ID",
            consumer_secret="FAKE_CLIENT_SECRET",
        )

    def start_discovery(self, source: OAuthSource | None = None) -> dict:
        """Start allowlist discovery and return the API response."""
        response = self.client.post(
            f"/api/v3/sources/oauth/dingtalk-allowlist/{(source or self.source).slug}/"
            "discover/start/"
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def callback_url(self, source: OAuthSource | None = None) -> str:
        """Build the allowlist discovery callback URL."""
        return reverse(
            "authentik_sources_oauth:dingtalk-allowlist-callback",
            kwargs={"source_slug": (source or self.source).slug},
        )

    def mock_successful_discovery_callback(self, mocker: Mocker):
        """Mock DingTalk discovery token/profile endpoints."""
        token_mock = mocker.post(
            DINGTALK_ACCESS_TOKEN_URL,
            json={"accessToken": "FAKE_USER_TOKEN", "corpId": "CORP_FAKE"},
        )
        profile_mock = mocker.get(
            DINGTALK_PROFILE_URL,
            json={"unionId": "UNION_FAKE", "corpId": "CORP_FAKE", "nick": "Ada"},
        )
        mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, status_code=403, json={"errcode": 88})
        return token_mock, profile_mock

    def auth_session_snapshot(self) -> dict[str, str | None]:
        """Return only the Django auth session keys."""
        session = self.client.session
        return {
            SESSION_KEY: session.get(SESSION_KEY),
            BACKEND_SESSION_KEY: session.get(BACKEND_SESSION_KEY),
            HASH_SESSION_KEY: session.get(HASH_SESSION_KEY),
        }

    def test_status_reports_policy_binding_guard_and_callback(self):
        """Status returns parsed allowlist and policy/binding/guard state."""
        policy = ExpressionPolicy.objects.create(
            name="managed-dingtalk",
            expression=render_dingtalk_allowlist_policy(
                {"companies": [{"corp_id": "CORP_FAKE", "allow_all": True}]}
            ),
        )
        PolicyBinding.objects.create(target=self.source, policy=policy, order=0, enabled=True)

        response = self.client.get(
            f"/api/v3/sources/oauth/dingtalk-allowlist/{self.source.slug}/status/"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            data["config"],
            {
                "companies": [
                    {
                        "allow_all": True,
                        "corp_id": "CORP_FAKE",
                        "dept_ids": [],
                        "label": "",
                    }
                ]
            },
        )
        self.assertTrue(data["managed_policy"]["exists"])
        self.assertTrue(data["policy_binding"]["exists"])
        self.assertTrue(data["policy_binding"]["enabled"])
        self.assertTrue(data["source_link_guard"]["enabled"])
        self.assertTrue(data["sourceLinkGuard"])
        self.assertEqual(
            data["callback_url"],
            "http://testserver/source/oauth/callback/dingtalk-test/",
        )

    def test_status_finds_managed_policy_bound_to_authentication_flow(self):
        """Status finds the UI-created allowlist binding on the source authentication flow."""
        self.source.authentication_flow = create_test_flow()
        self.source.save()
        policy = ExpressionPolicy.objects.create(
            name="managed-dingtalk",
            expression=render_dingtalk_allowlist_policy(
                {"companies": [{"corp_id": "CORP_FAKE", "dept_ids": [10]}]}
            ),
        )
        PolicyBinding.objects.create(
            target=self.source.authentication_flow,
            policy=policy,
            order=0,
            enabled=True,
        )

        response = self.client.get(
            f"/api/v3/sources/oauth/dingtalk-allowlist/{self.source.slug}/status/"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["managed_policy"]["exists"])
        self.assertTrue(data["policy_binding"]["exists"])
        self.assertTrue(data["source_link_guard"]["enabled"])
        self.assertEqual(data["config"]["companies"][0]["dept_ids"], ["10"])

    def test_status_prefers_enabled_binding_over_stale_disabled_binding(self):
        """Status guard uses an enabled marked binding when a stale disabled one sorts first."""
        stale_policy = ExpressionPolicy.objects.create(
            name="stale-dingtalk",
            expression=render_dingtalk_allowlist_policy(
                {"companies": [{"corp_id": "CORP_STALE", "allow_all": True}]}
            ),
        )
        enabled_policy = ExpressionPolicy.objects.create(
            name="enabled-dingtalk",
            expression=render_dingtalk_allowlist_policy(
                {"companies": [{"corp_id": "CORP_ENABLED", "dept_ids": [10]}]}
            ),
        )
        PolicyBinding.objects.create(
            target=self.source,
            policy=stale_policy,
            order=0,
            enabled=False,
        )
        enabled_binding = PolicyBinding.objects.create(
            target=self.source,
            policy=enabled_policy,
            order=1,
            enabled=True,
        )

        response = self.client.get(
            f"/api/v3/sources/oauth/dingtalk-allowlist/{self.source.slug}/status/"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["managed_policy"]["exists"])
        self.assertEqual(data["managed_policy"]["name"], "enabled-dingtalk")
        self.assertTrue(data["policy_binding"]["enabled"])
        self.assertEqual(data["policy_binding"]["pk"], str(enabled_binding.pk))
        self.assertTrue(data["source_link_guard"]["enabled"])
        self.assertTrue(data["sourceLinkGuard"])
        self.assertEqual(data["config"]["companies"][0]["corp_id"], "CORP_ENABLED")

    def test_discover_start_returns_signed_state_and_dingtalk_authorize_url(self):
        """Discovery start uses source credentials, scopes, standard callback, and signed state."""
        data = self.start_discovery()
        signed = signing.loads(data["state"], salt="authentik.sources.oauth.dingtalk.allowlist")
        self.assertEqual(signed["source_slug"], self.source.slug)
        self.assertIsInstance(signed["nonce"], str)
        self.assertGreater(len(signed["nonce"]), 16)
        parsed = urlparse(data["authorization_url"])
        qs = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "login.dingtalk.com")
        self.assertEqual(qs["client_id"], ["FAKE_CLIENT_ID"])
        self.assertEqual(
            qs["redirect_uri"],
            ["http://testserver/source/oauth/callback/dingtalk-test/"],
        )
        self.assertEqual(qs["scope"], ["openid corpid Contact.User.Read"])
        self.assertEqual(qs["state"], [data["state"]])
        self.assertNotIn("FAKE_CLIENT_SECRET", data["authorization_url"])
        self.assertEqual(data["url"], data["authorization_url"])

    def test_standard_oauth_callback_handles_discovery_state_without_authenticating(self):
        """Standard DingTalk callback returns popup discovery result for discovery state."""
        data = self.start_discovery()
        auth_session_before = self.auth_session_snapshot()

        with Mocker() as mocker:
            token_mock, profile_mock = self.mock_successful_discovery_callback(mocker)
            response = self.client.get(
                reverse(
                    "authentik_sources_oauth:oauth-client-callback",
                    kwargs={"source_slug": self.source.slug},
                ),
                {"code": "AUTH_CODE", "state": data["state"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"window.opener.postMessage", response.content)
        self.assertIn(b'"corpId": "CORP_FAKE"', response.content)
        self.assertEqual(UserOAuthSourceConnection.objects.count(), 0)
        self.assertEqual(self.auth_session_snapshot(), auth_session_before)
        self.assertTrue(token_mock.called)
        self.assertTrue(profile_mock.called)

    def test_discovery_callback_posts_company_label_from_org_auth_info(self):
        """Discovery callback enriches the selected corp with a human-readable company label."""
        data = self.start_discovery()

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
            org_mock = mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={
                    "authOrgInfo": {
                        "corpId": "CORP_FAKE",
                        "corpName": "示例公司",
                    }
                },
            )

            response = self.client.get(
                reverse(
                    "authentik_sources_oauth:oauth-client-callback",
                    kwargs={"source_slug": self.source.slug},
                ),
                {"code": "AUTH_CODE", "state": data["state"]},
            )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('"label": "示例公司"', content)
        self.assertIn('"corp_name": "示例公司"', content)
        self.assertEqual(
            parse_qs(urlparse(org_mock.last_request.url).query)["targetCorpId"], ["CORP_FAKE"]
        )
        self.assertEqual(
            org_mock.last_request.headers["x-acs-dingtalk-access-token"], "FAKE_APP_TOKEN"
        )

    def test_company_label_extractor_accepts_nested_auth_info_arrays(self):
        """DingTalk org auth responses can nest the contact name in auth info lists."""
        self.assertEqual(
            _extract_dingtalk_corp_label(
                {
                    "authInfos": [
                        {
                            "targetCorpId": "CORP_FAKE",
                            "contactName": "示例通讯录",
                        }
                    ]
                }
            ),
            "示例通讯录",
        )

    def test_non_admin_cannot_use_server_side_discovery_endpoints(self):
        """Server-side DingTalk endpoints require source read permissions."""
        self.client.force_login(create_test_user())

        for method, path, body in [
            (
                self.client.get,
                f"/api/v3/sources/oauth/dingtalk-allowlist/{self.source.slug}/status/",
                {},
            ),
            (
                self.client.post,
                f"/api/v3/sources/oauth/dingtalk-allowlist/{self.source.slug}/discover/start/",
                {},
            ),
            (
                self.client.post,
                f"/api/v3/sources/oauth/dingtalk-allowlist/{self.source.slug}/departments/",
                {"corp_id": "CORP_FAKE"},
            ),
        ]:
            response = method(path, body, format="json")
            self.assertEqual(response.status_code, 403)

    def test_departments_fetches_and_normalizes_flat_list_without_secrets(self):
        """Departments API fetches via server-side credentials and omits secrets/tokens."""
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "FAKE_APP_TOKEN"})
            org_mock = mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={
                    "auth_org_info": {
                        "corpid": "CORP_FAKE",
                        "corp_name": "示例公司",
                    }
                },
            )
            mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                [
                    {
                        "json": {
                            "errcode": 0,
                            "result": [
                                {"dept_id": 10, "name": "Engineering", "parent_id": 1},
                                {"dept_id": 20, "name": "Finance", "parent_id": 1},
                            ],
                        }
                    },
                    {"json": {"errcode": 0, "result": []}},
                    {
                        "json": {
                            "errcode": 0,
                            "result": [
                                {"dept_id": 21, "name": "Payroll", "parent_id": 20},
                            ],
                        }
                    },
                    {"json": {"errcode": 0, "result": []}},
                ],
            )

            response = self.client.post(
                f"/api/v3/sources/oauth/dingtalk-allowlist/{self.source.slug}/departments/",
                {"corp_id": "CORP_FAKE"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["corp_id"], "CORP_FAKE")
        self.assertEqual(data["label"], "示例公司")
        self.assertEqual(
            data["departments"],
            [
                {"dept_id": "10", "name": "Engineering", "parent_id": "1"},
                {"dept_id": "20", "name": "Finance", "parent_id": "1"},
                {"dept_id": "21", "name": "Payroll", "parent_id": "20"},
            ],
        )
        self.assertNotIn("FAKE_CLIENT_SECRET", response.content.decode())
        self.assertNotIn("FAKE_APP_TOKEN", response.content.decode())
        token_request_qs = parse_qs(urlparse(mocker.request_history[0].url).query)
        self.assertEqual(
            token_request_qs,
            {"appkey": ["FAKE_CLIENT_ID"], "appsecret": ["FAKE_CLIENT_SECRET"]},
        )
        self.assertEqual(
            parse_qs(urlparse(org_mock.last_request.url).query)["targetCorpId"], ["CORP_FAKE"]
        )

    def test_departments_rejects_unverified_target_corp(self):
        """Departments API does not show the app-bound company's departments for another corp."""
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "FAKE_APP_TOKEN"})
            mocker.get(DINGTALK_ORG_AUTH_INFO_URL, json={"errcode": 400002, "errmsg": "denied"})
            department_mock = mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                [
                    {
                        "json": {
                            "errcode": 0,
                            "result": [
                                {"dept_id": 10, "name": "Engineering", "parent_id": 1},
                            ],
                        }
                    },
                    {"json": {"errcode": 0, "result": []}},
                ],
            )

            response = self.client.post(
                f"/api/v3/sources/oauth/dingtalk-allowlist/{self.source.slug}/departments/",
                {"corp_id": "CORP_FAKE"},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(department_mock.called)
        content = response.content.decode()
        self.assertIn("authorized by this DingTalk application", content)
        self.assertNotIn("FAKE_CLIENT_SECRET", content)
        self.assertNotIn("FAKE_APP_TOKEN", content)

    def test_departments_error_response_does_not_leak_credentials(self):
        """Departments API hides upstream exception text containing query-string secrets."""
        leaked_error = (
            "403 Client Error for url: "
            "https://oapi.dingtalk.com/topapi/v2/department/listsub?"
            "appsecret=FAKE_CLIENT_SECRET&access_token=FAKE_APP_TOKEN&"
            "consumer_secret=FAKE_CONSUMER_SECRET"
        )
        with patch(
            "authentik.sources.oauth.api.dingtalk_allowlist.fetch_dingtalk_departments",
            side_effect=RequestException(leaked_error),
        ):
            response = self.client.post(
                f"/api/v3/sources/oauth/dingtalk-allowlist/{self.source.slug}/departments/",
                {"corp_id": "CORP_FAKE"},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        content = response.content.decode()
        self.assertIn("Could not fetch DingTalk departments.", content)
        self.assertNotIn("FAKE_CLIENT_SECRET", content)
        self.assertNotIn("FAKE_APP_TOKEN", content)
        self.assertNotIn("FAKE_CONSUMER_SECRET", content)
        self.assertNotIn("appsecret", content)
        self.assertNotIn("access_token", content)
        self.assertNotIn("consumer_secret", content)

    def test_departments_depth_limit_raises_stable_error(self):
        """Department traversal fails closed when the configured depth limit is exceeded."""
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "FAKE_APP_TOKEN"})
            mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={"authOrgInfo": {"corpId": "CORP_FAKE", "corpName": "Fake Company"}},
            )
            mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                [
                    {"json": {"errcode": 0, "result": [{"dept_id": 10, "name": "Root"}]}},
                    {"json": {"errcode": 0, "result": [{"dept_id": 20, "name": "Child"}]}},
                ],
            )
            with patch("authentik.sources.oauth.types.dingtalk.DINGTALK_MAX_DEPARTMENT_DEPTH", 1):
                with self.assertRaisesMessage(
                    ValueError,
                    "DingTalk department traversal depth limit exceeded.",
                ):
                    fetch_dingtalk_departments(self.source, "CORP_FAKE")

    def test_departments_count_limit_raises_stable_error(self):
        """Department traversal fails closed when the configured department limit is exceeded."""
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "FAKE_APP_TOKEN"})
            mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={"authOrgInfo": {"corpId": "CORP_FAKE", "corpName": "Fake Company"}},
            )
            mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                [
                    {
                        "json": {
                            "errcode": 0,
                            "result": [
                                {"dept_id": 10, "name": "Engineering"},
                                {"dept_id": 20, "name": "Finance"},
                            ],
                        }
                    },
                ],
            )
            with patch("authentik.sources.oauth.types.dingtalk.DINGTALK_MAX_DEPARTMENTS", 1):
                with self.assertRaisesMessage(
                    ValueError,
                    "DingTalk department traversal department limit exceeded.",
                ):
                    fetch_dingtalk_departments(self.source, "CORP_FAKE")

    def test_plain_callback_posts_result_to_opener_without_authenticating(self):
        """Discovery callback exchanges code and posts selected corp/profile to opener."""
        state = self.start_discovery()["state"]
        auth_snapshot = self.auth_session_snapshot()

        with Mocker() as mocker:
            self.mock_successful_discovery_callback(mocker)

            response = self.client.get(
                self.callback_url(),
                {"authCode": "AUTH_CODE", "state": state},
            )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("window.opener.postMessage", content)
        self.assertIn('"corp_id": "CORP_FAKE"', content)
        self.assertIn('"corpId": "CORP_FAKE"', content)
        self.assertIn('"unionId": "UNION_FAKE"', content)
        self.assertNotIn("FAKE_USER_TOKEN", content)
        self.assertNotIn("FAKE_CLIENT_SECRET", content)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())
        self.assertEqual(self.auth_session_snapshot(), auth_snapshot)
        self.assertNotIn(SESSION_KEY_PLAN, self.client.session)

    def test_discovery_callback_rejects_replayed_state(self):
        """Discovery callback consumes state once and rejects a second use."""
        state = self.start_discovery()["state"]

        with Mocker() as mocker:
            token_mock, profile_mock = self.mock_successful_discovery_callback(mocker)

            first_response = self.client.get(
                self.callback_url(),
                {"authCode": "AUTH_CODE", "state": state},
            )
            second_response = self.client.get(
                self.callback_url(),
                {"authCode": "AUTH_CODE", "state": state},
            )

        self.assertEqual(first_response.status_code, 200)
        self.assertIn('"ok": true', first_response.content.decode())
        self.assertEqual(second_response.status_code, 200)
        self.assertIn('"ok": false', second_response.content.decode())
        self.assertIn("already been used", second_response.content.decode())
        self.assertEqual(token_mock.call_count, 1)
        self.assertEqual(profile_mock.call_count, 1)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())

    def test_discovery_callback_rejects_state_for_wrong_source_slug(self):
        """Discovery callback rejects a valid state replayed against another source slug."""
        other_source = OAuthSource.objects.create(
            name="Other DingTalk Test",
            slug="dingtalk-other",
            provider_type="dingtalk",
            consumer_key="OTHER_CLIENT_ID",
            consumer_secret="OTHER_CLIENT_SECRET",
        )
        state = self.start_discovery()["state"]

        with Mocker() as mocker:
            token_mock, profile_mock = self.mock_successful_discovery_callback(mocker)
            response = self.client.get(
                self.callback_url(other_source),
                {"authCode": "AUTH_CODE", "state": state},
            )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('"ok": false', content)
        self.assertIn("source mismatch", content)
        self.assertEqual(token_mock.call_count, 0)
        self.assertEqual(profile_mock.call_count, 0)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=other_source).exists())

    def test_discovery_callback_rejects_expired_state(self):
        """Discovery callback rejects signed state older than the allowed age."""
        with patch("django.core.signing.time.time", return_value=1_000):
            state = self.start_discovery()["state"]

        with Mocker() as mocker:
            token_mock, profile_mock = self.mock_successful_discovery_callback(mocker)
            with patch("django.core.signing.time.time", return_value=1_601):
                response = self.client.get(
                    self.callback_url(),
                    {"authCode": "AUTH_CODE", "state": state},
                )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('"ok": false', content)
        self.assertIn("Signature age", content)
        self.assertEqual(token_mock.call_count, 0)
        self.assertEqual(profile_mock.call_count, 0)
        self.assertFalse(UserOAuthSourceConnection.objects.filter(source=self.source).exists())
