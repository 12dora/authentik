"""DingTalk Type tests"""

from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

from django.test import RequestFactory, TestCase
from django.urls import reverse
from requests_mock import Mocker

from authentik.core.sources.stage import PLAN_CONTEXT_SOURCES_CONNECTION
from authentik.core.tests.utils import create_test_admin_user, create_test_flow
from authentik.flows.views.executor import SESSION_KEY_PLAN
from authentik.lib.utils.reflection import all_subclasses
from authentik.sources.oauth.models import DingTalkOAuthSource, OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_ACCESS_TOKEN_URL,
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_GET_BY_UNION_ID_URL,
    DINGTALK_PROFILE_URL,
    DINGTALK_USER_DETAIL_URL,
    DingTalkOAuth2Callback,
    DingTalkOAuth2Client,
    DingTalkType,
)
from authentik.sources.oauth.types.registry import registry
from authentik.stages.prompt.stage import PLAN_CONTEXT_PROMPT

DINGTALK_ME_PROFILE = {
    "nick": "Ada",
    "avatarUrl": "https://example.invalid/avatar.png",
    "mobile": "13800000000",
    "openId": "OPEN_ID",
    "unionId": "UNION_ID",
    "email": "ada@example.invalid",
    "stateCode": "86",
    "corpId": "CORP_ID",
}

GET_BY_UNION_ID_RESPONSE = {"errcode": 0, "result": {"userid": "USER_ID"}}

USER_DETAIL_RESPONSE = {
    "errcode": 0,
    "result": {
        "userid": "USER_ID",
        "name": "Ada Lovelace",
        "avatar": "https://example.invalid/detail-avatar.png",
        "title": "Principal Engineer",
        "email": "ada@company.example",
        "mobile": "13800000000",
        "dept_id_list": [1, 2],
        "job_number": "E-001",
        "role_list": [{"id": 10, "name": "Admin"}],
    },
}


class TestTypeDingTalk(TestCase):
    """OAuth Source tests"""

    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="test",
            slug="test",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )
        self.factory = RequestFactory()

    def get_callback_request(self, **query):
        """Build a callback request with a matching OAuth state."""
        request = self.factory.get("/", query)
        request.session = {"oauth-client-test-request-state": query["state"]}
        return request

    def test_redirect_uses_dingtalk_authorize_url(self):
        """Test DingTalk authorize URL"""
        res = self.client.get(
            reverse(
                "authentik_sources_oauth:oauth-client-login",
                kwargs={"source_slug": self.source.slug},
            )
        )

        self.assertEqual(res.status_code, 302)
        parsed = urlparse(res.url)
        qs = parse_qs(parsed.query)
        state = self.client.session["oauth-client-test-request-state"]

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "login.dingtalk.com")
        self.assertEqual(parsed.path, "/oauth2/auth")
        self.assertEqual(qs["client_id"], ["CLIENT_ID"])
        self.assertEqual(qs["redirect_uri"], ["http://testserver/source/oauth/callback/test/"])
        self.assertEqual(qs["response_type"], ["code"])
        self.assertEqual(qs["scope"], ["openid corpid Contact.User.Read"])
        self.assertEqual(qs["prompt"], ["consent"])
        self.assertEqual(qs["state"], [state])

    def test_access_token_reads_auth_code_and_normalizes_response(self):
        """Test DingTalk token exchange"""
        request = self.get_callback_request(authCode="AUTH_CODE", state="STATE")
        client = DingTalkOAuth2Client(
            self.source,
            request,
            callback="/source/oauth/callback/test/",
        )

        with Mocker() as mocker:
            token_request = mocker.post(
                DINGTALK_ACCESS_TOKEN_URL,
                json={
                    "accessToken": "USER_ACCESS_TOKEN",
                    "refreshToken": "REFRESH_TOKEN",
                    "expireIn": 7200,
                    "corpId": "CORP_ID",
                },
            )
            token = client.get_access_token()

        self.assertEqual(
            token_request.last_request.json(),
            {
                "clientId": "CLIENT_ID",
                "clientSecret": "CLIENT_SECRET",
                "code": "AUTH_CODE",
                "grantType": "authorization_code",
            },
        )
        self.assertEqual(token["access_token"], "USER_ACCESS_TOKEN")
        self.assertEqual(token["refresh_token"], "REFRESH_TOKEN")
        self.assertEqual(token["expires_in"], 7200)
        self.assertEqual(token["token_type"], "Bearer")
        self.assertEqual(token["corp_id"], "CORP_ID")

    def test_access_token_falls_back_to_code_parameter(self):
        """Test DingTalk token exchange defensive code fallback"""
        request = self.get_callback_request(code="AUTH_CODE", state="STATE")
        client = DingTalkOAuth2Client(
            self.source,
            request,
            callback="/source/oauth/callback/test/",
        )

        with Mocker() as mocker:
            token_request = mocker.post(
                DINGTALK_ACCESS_TOKEN_URL,
                json={"accessToken": "USER_ACCESS_TOKEN", "expireIn": 7200},
            )
            token = client.get_access_token()

        self.assertEqual(token_request.last_request.json()["code"], "AUTH_CODE")
        self.assertEqual(token["access_token"], "USER_ACCESS_TOKEN")

    def test_access_token_handles_non_json_error_response(self):
        """Test DingTalk token exchange with a non-JSON error response"""
        request = self.get_callback_request(authCode="AUTH_CODE", state="STATE")
        client = DingTalkOAuth2Client(
            self.source,
            request,
            callback="/source/oauth/callback/test/",
        )

        with Mocker() as mocker:
            mocker.post(DINGTALK_ACCESS_TOKEN_URL, status_code=500, text="upstream unavailable")
            token = client.get_access_token()

        self.assertEqual(token, {"error": "DingTalk token exchange failed."})

    def test_access_token_handles_dingtalk_error_response(self):
        """Test DingTalk token exchange preserves DingTalk error details"""
        request = self.get_callback_request(authCode="AUTH_CODE", state="STATE")
        client = DingTalkOAuth2Client(
            self.source,
            request,
            callback="/source/oauth/callback/test/",
        )

        with Mocker() as mocker:
            mocker.post(
                DINGTALK_ACCESS_TOKEN_URL,
                status_code=400,
                json={"errorCode": "invalid_grant", "message": "authCode expired"},
            )
            token = client.get_access_token()

        self.assertEqual(token, {"error": "invalid_grant"})

    def test_profile_fetch_returns_base_profile(self):
        """Test DingTalk profile fetch"""
        request = self.factory.get("/")
        request.session = {}
        client = DingTalkOAuth2Client(self.source, request)

        with Mocker() as mocker:
            mocker.get(DINGTALK_PROFILE_URL, json=DINGTALK_ME_PROFILE)
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, status_code=403, json={"errcode": 88})
            profile = client.get_profile_info(
                {"access_token": "USER_ACCESS_TOKEN", "token_type": "Bearer"}
            )

        self.assertEqual(
            mocker.request_history[0].headers["x-acs-dingtalk-access-token"],
            "USER_ACCESS_TOKEN",
        )
        self.assertNotIn("Authorization", mocker.request_history[0].headers)
        self.assertNotIn("access_token", parse_qs(urlparse(mocker.request_history[0].url).query))
        self.assertEqual(profile["unionId"], "UNION_ID")
        self.assertEqual(profile["openId"], "OPEN_ID")
        self.assertEqual(profile["nick"], "Ada")
        self.assertEqual(profile["avatarUrl"], "https://example.invalid/avatar.png")
        self.assertEqual(profile["email"], "ada@example.invalid")

    def test_profile_fetch_merges_token_corp_id(self):
        """Test DingTalk profile fetch preserves the OAuth-selected organization."""
        request = self.factory.get("/")
        request.session = {}
        client = DingTalkOAuth2Client(self.source, request)

        profile_without_corp = DINGTALK_ME_PROFILE.copy()
        profile_without_corp.pop("corpId")
        with Mocker() as mocker:
            mocker.get(DINGTALK_PROFILE_URL, json=profile_without_corp)
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, status_code=403, json={"errcode": 88})
            profile = client.get_profile_info(
                {
                    "access_token": "USER_ACCESS_TOKEN",
                    "token_type": "Bearer",
                    "corp_id": "CORP_ID",
                }
            )

        self.assertEqual(profile["corpId"], "CORP_ID")

    def test_profile_fetch_merges_enhanced_directory_profile(self):
        """Test DingTalk enhanced directory profile fetch"""
        request = self.factory.get("/")
        request.session = {}
        client = DingTalkOAuth2Client(self.source, request)

        with Mocker() as mocker:
            mocker.get(DINGTALK_PROFILE_URL, json=DINGTALK_ME_PROFILE)
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_ACCESS_TOKEN"})
            mocker.post(DINGTALK_GET_BY_UNION_ID_URL, json=GET_BY_UNION_ID_RESPONSE)
            mocker.post(DINGTALK_USER_DETAIL_URL, json=USER_DETAIL_RESPONSE)

            profile = client.get_profile_info(
                {"access_token": "USER_ACCESS_TOKEN", "token_type": "Bearer"}
            )

        self.assertEqual(profile["unionId"], "UNION_ID")
        self.assertEqual(profile["openId"], "OPEN_ID")
        self.assertEqual(profile["nick"], "Ada")
        self.assertEqual(profile["userid"], "USER_ID")
        self.assertEqual(profile["name"], "Ada Lovelace")
        self.assertEqual(profile["avatar"], "https://example.invalid/detail-avatar.png")
        self.assertEqual(profile["title"], "Principal Engineer")
        self.assertEqual(profile["email"], "ada@company.example")
        self.assertEqual(profile["dept_id_list"], [1, 2])
        self.assertEqual(profile["job_number"], "E-001")
        self.assertEqual(profile["role_list"], [{"id": 10, "name": "Admin"}])

    def test_profile_fetch_ignores_enhanced_directory_failure(self):
        """Test DingTalk profile fallback when directory permissions are missing"""
        request = self.factory.get("/")
        request.session = {}
        client = DingTalkOAuth2Client(self.source, request)

        with Mocker() as mocker:
            mocker.get(DINGTALK_PROFILE_URL, json=DINGTALK_ME_PROFILE)
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, status_code=403, json={"errcode": 88})

            profile = client.get_profile_info(
                {"access_token": "USER_ACCESS_TOKEN", "token_type": "Bearer"}
            )

        self.assertEqual(profile["unionId"], "UNION_ID")
        self.assertEqual(profile["nick"], "Ada")
        self.assertNotIn("title", profile)

    def test_profile_fetch_does_not_log_app_secret_on_enhancement_failure(self):
        """Test DingTalk enhanced profile failures don't log the app secret"""
        request = self.factory.get("/")
        request.session = {}
        client = DingTalkOAuth2Client(self.source, request)
        client.logger = Mock()

        with Mocker() as mocker:
            mocker.get(DINGTALK_PROFILE_URL, json=DINGTALK_ME_PROFILE)
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, status_code=403, json={"errcode": 88})

            profile = client.get_profile_info(
                {"access_token": "USER_ACCESS_TOKEN", "token_type": "Bearer"}
            )

        self.assertEqual(profile["unionId"], "UNION_ID")
        log_output = " ".join(str(call) for call in client.logger.warning.call_args_list)
        self.assertNotIn("CLIENT_SECRET", log_output)

    def test_user_id(self):
        """Test DingTalk user ID extraction"""
        callback = DingTalkOAuth2Callback()

        self.assertEqual(
            callback.get_user_id({"corpId": "CORP_ID", "userid": "USER_ID"}),
            "CORP_ID:USER_ID",
        )
        self.assertEqual(callback.get_user_id({"userid": "USER_ID"}), "USER_ID")
        self.assertEqual(callback.get_user_id({"unionId": "UNION_ID"}), "UNION_ID")
        self.assertEqual(callback.get_user_id({"openId": "OPEN_ID"}), "OPEN_ID")
        self.assertIsNone(callback.get_user_id({}))

    def test_base_user_properties(self):
        """Test DingTalk Enrollment context"""
        profile = DINGTALK_ME_PROFILE | USER_DETAIL_RESPONSE["result"]
        context = DingTalkType().get_base_user_properties(
            source=self.source, info=profile, client=None, token={}
        )

        self.assertEqual(context["username"], "CORP_ID:USER_ID")
        self.assertEqual(context["email"], "ada@company.example")
        self.assertEqual(context["name"], "Ada Lovelace")
        self.assertEqual(context["attributes"]["dingtalk"]["union_id"], "UNION_ID")
        self.assertEqual(context["attributes"]["dingtalk"]["open_id"], "OPEN_ID")
        self.assertEqual(context["attributes"]["dingtalk"]["user_id"], "USER_ID")
        self.assertEqual(context["attributes"]["dingtalk"]["corp_id"], "CORP_ID")
        self.assertEqual(context["attributes"]["dingtalk"]["nick"], "Ada")
        self.assertEqual(context["attributes"]["dingtalk"]["title"], "Principal Engineer")
        self.assertEqual(
            context["attributes"]["dingtalk"]["avatar"],
            "https://example.invalid/detail-avatar.png",
        )
        self.assertEqual(context["attributes"]["dingtalk"]["raw_profile"], profile)

    def test_registry(self):
        """Test DingTalk registry entry"""
        oauth_type = registry.find_type("dingtalk")

        self.assertEqual(oauth_type.name, "dingtalk")
        self.assertEqual(oauth_type.verbose_name, "DingTalk")

    def test_creatable_source_type(self):
        """Test DingTalk source appears in creatable source types"""
        self.assertIn(DingTalkOAuthSource, all_subclasses(OAuthSource))
        self.assertTrue(DingTalkOAuthSource._meta.abstract)
        self.assertEqual(str(DingTalkOAuthSource._meta.verbose_name), "DingTalk OAuth Source")
        self.assertEqual(DingTalkOAuthSource._meta.model_name, "dingtalkoauthsource")

        source = DingTalkOAuthSource.__new__(DingTalkOAuthSource)
        source.Meta.abstract = True
        self.assertEqual(source.component, "ak-source-oauth-form")
        self.assertEqual(source.icon_url, "/static/authentik/sources/dingtalk.svg")

    def test_api_source_types_lists_dingtalk(self):
        """Test DingTalk source type appears in the Admin API source type list"""
        self.client.force_login(create_test_admin_user())
        response = self.client.get(reverse("authentik_api:oauthsource-source-types"))

        self.assertEqual(response.status_code, 200)
        dingtalk = [
            source_type for source_type in response.json() if source_type["name"] == "dingtalk"
        ]
        self.assertEqual(len(dingtalk), 1)
        self.assertEqual(dingtalk[0]["verbose_name"], "DingTalk")
        self.assertFalse(dingtalk[0]["urls_customizable"])
        self.assertEqual(dingtalk[0]["authorization_url"], "https://login.dingtalk.com/oauth2/auth")
        self.assertEqual(
            dingtalk[0]["access_token_url"],
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
        )
        self.assertEqual(
            dingtalk[0]["profile_url"],
            "https://api.dingtalk.com/v1.0/contact/users/me",
        )

    def test_callback_builds_flow_plan_with_dingtalk_profile_attributes(self):
        """Test DingTalk callback stores profile details for enrollment/write stages"""
        self.source.enrollment_flow = create_test_flow()
        self.source.save()

        login_response = self.client.get(
            reverse(
                "authentik_sources_oauth:oauth-client-login",
                kwargs={"source_slug": self.source.slug},
            )
        )
        self.assertEqual(login_response.status_code, 302)
        state = self.client.session["oauth-client-test-request-state"]

        with Mocker() as mocker:
            mocker.post(
                DINGTALK_ACCESS_TOKEN_URL,
                json={
                    "accessToken": "USER_ACCESS_TOKEN",
                    "refreshToken": "REFRESH_TOKEN",
                    "expireIn": 7200,
                },
            )
            mocker.get(DINGTALK_PROFILE_URL, json=DINGTALK_ME_PROFILE)
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_ACCESS_TOKEN"})
            mocker.post(DINGTALK_GET_BY_UNION_ID_URL, json=GET_BY_UNION_ID_RESPONSE)
            mocker.post(DINGTALK_USER_DETAIL_URL, json=USER_DETAIL_RESPONSE)

            callback_response = self.client.get(
                reverse(
                    "authentik_sources_oauth:oauth-client-callback",
                    kwargs={"source_slug": self.source.slug},
                ),
                {"authCode": "AUTH_CODE", "state": state},
            )

        self.assertEqual(callback_response.status_code, 302)

        plan = self.client.session[SESSION_KEY_PLAN]
        prompt_data = plan.context[PLAN_CONTEXT_PROMPT]
        dingtalk = prompt_data["attributes"]["dingtalk"]
        connection = plan.context[PLAN_CONTEXT_SOURCES_CONNECTION]

        self.assertEqual(prompt_data["username"], "CORP_ID:USER_ID")
        self.assertEqual(prompt_data["email"], "ada@company.example")
        self.assertEqual(prompt_data["name"], "Ada Lovelace")
        self.assertEqual(dingtalk["name"], "Ada Lovelace")
        self.assertEqual(dingtalk["title"], "Principal Engineer")
        self.assertEqual(dingtalk["avatar"], "https://example.invalid/detail-avatar.png")
        self.assertEqual(dingtalk["user_id"], "USER_ID")
        self.assertEqual(dingtalk["corp_id"], "CORP_ID")
        self.assertEqual(dingtalk["raw_profile"], DINGTALK_ME_PROFILE)
        self.assertNotIn("title", dingtalk["raw_profile"])
        self.assertNotIn("userid", dingtalk["raw_profile"])
        self.assertEqual(connection.identifier, "CORP_ID:USER_ID")
        self.assertEqual(connection.access_token, "USER_ACCESS_TOKEN")
        self.assertEqual(connection.refresh_token, "REFRESH_TOKEN")
