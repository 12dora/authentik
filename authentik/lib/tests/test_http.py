"""Test HTTP Helpers"""

from unittest.mock import patch

from django.test import RequestFactory, TestCase
from requests_mock import Mocker

from authentik.core.models import Token, TokenIntents, UserTypes
from authentik.core.tests.utils import create_test_admin_user
from authentik.lib.utils.http import DebugSession
from authentik.lib.views import bad_request_message
from authentik.root.middleware import ClientIPMiddleware


class TestHTTP(TestCase):
    """Test HTTP Helpers"""

    def setUp(self) -> None:
        self.user = create_test_admin_user()
        self.factory = RequestFactory()

    def test_bad_request_message(self):
        """test bad_request_message"""
        request = self.factory.get("/")
        self.assertEqual(bad_request_message(request, "foo").status_code, 400)

    def test_normal(self):
        """Test normal request"""
        request = self.factory.get("/")
        self.assertEqual(ClientIPMiddleware.get_client_ip(request), "127.0.0.1")

    def test_forward_for(self):
        """Test x-forwarded-for request"""
        request = self.factory.get("/", HTTP_X_FORWARDED_FOR="127.0.0.2")
        self.assertEqual(ClientIPMiddleware.get_client_ip(request), "127.0.0.2")

    def test_forward_for_invalid(self):
        """Test invalid forward for"""
        request = self.factory.get("/", HTTP_X_FORWARDED_FOR="foobar")
        self.assertEqual(ClientIPMiddleware.get_client_ip(request), ClientIPMiddleware.default_ip)

    def test_fake_outpost(self):
        """Test faked IP which is overridden by an outpost"""
        token = Token.objects.create(
            identifier="test", user=self.user, intent=TokenIntents.INTENT_API
        )
        # Invalid, non-existent token
        request = self.factory.get(
            "/",
            **{
                ClientIPMiddleware.outpost_remote_ip_header: "1.2.3.4",
                ClientIPMiddleware.outpost_token_header: "abc",
            },
        )
        self.assertEqual(ClientIPMiddleware.get_client_ip(request), "127.0.0.1")
        # Invalid, user doesn't have permissions
        request = self.factory.get(
            "/",
            **{
                ClientIPMiddleware.outpost_remote_ip_header: "1.2.3.4",
                ClientIPMiddleware.outpost_token_header: token.key,
            },
        )
        self.assertEqual(ClientIPMiddleware.get_client_ip(request), "127.0.0.1")
        # Invalid, not a real IP
        self.user.type = UserTypes.INTERNAL_SERVICE_ACCOUNT
        self.user.save()
        request = self.factory.get(
            "/",
            **{
                ClientIPMiddleware.outpost_remote_ip_header: "foobar",
                ClientIPMiddleware.outpost_token_header: token.key,
            },
        )
        self.assertEqual(ClientIPMiddleware.get_client_ip(request), "127.0.0.1")
        # Valid
        self.user.type = UserTypes.INTERNAL_SERVICE_ACCOUNT
        self.user.save()
        request = self.factory.get(
            "/",
            **{
                ClientIPMiddleware.outpost_remote_ip_header: "1.2.3.4",
                ClientIPMiddleware.outpost_token_header: token.key,
            },
        )
        self.assertEqual(ClientIPMiddleware.get_client_ip(request), "1.2.3.4")

    @patch("authentik.lib.utils.http.LOGGER")
    def test_debug_session_redacts_sensitive_request_and_response_values(self, logger):
        """Debug HTTP logging keeps events but redacts credentials, tokens, and bodies."""
        with Mocker() as mocker:
            mocker.post(
                "https://example.invalid/token",
                json={"access_token": "RESPONSE_ACCESS_TOKEN", "name": "Ada"},
                headers={"Authorization": "Bearer RESPONSE_TOKEN"},
            )
            session = DebugSession()
            response = session.post(
                "https://example.invalid/token"
                "?appsecret=APP_SECRET&access_token=APP_TOKEN&safe=value",
                headers={
                    "Authorization": "Bearer REQUEST_TOKEN",
                    "x-acs-dingtalk-access-token": "DINGTALK_USER_TOKEN",
                    "x-request-id": "safe-id",
                },
                json={
                    "clientSecret": "CLIENT_SECRET",
                    "code": "AUTH_CODE",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(logger.debug.call_count, 2)
        log_output = " ".join(str(call) for call in logger.debug.call_args_list)
        self.assertIn("HTTP request sent", log_output)
        self.assertIn("HTTP response received", log_output)
        self.assertIn("safe=value", log_output)
        self.assertIn("x-request-id", log_output)
        for secret in [
            "APP_SECRET",
            "APP_TOKEN",
            "REQUEST_TOKEN",
            "DINGTALK_USER_TOKEN",
            "CLIENT_SECRET",
            "AUTH_CODE",
            "RESPONSE_ACCESS_TOKEN",
            "RESPONSE_TOKEN",
            "Ada",
        ]:
            self.assertNotIn(secret, log_output)
