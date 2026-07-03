"""EasyAuth DingTalk managed-users API tests."""

from unittest.mock import patch

from django.test import TestCase

from authentik.core.tests.utils import create_test_user
from authentik.sources.oauth.dingtalk.managed_users import (
    DingTalkBindingConflict,
    DingTalkManagerNotFound,
)
from authentik.sources.oauth.models import OAuthSource

SENSITIVE_RESPONSE_KEYS = {
    "consumer_secret",
    "email",
    "mobile",
    "open_id",
    "raw",
    "union_id",
}


def assert_sensitive_keys_absent(test_case, value):
    if isinstance(value, dict):
        for key, item in value.items():
            test_case.assertNotIn(key, SENSITIVE_RESPONSE_KEYS)
            assert_sensitive_keys_absent(test_case, item)
    elif isinstance(value, list):
        for item in value:
            assert_sensitive_keys_absent(test_case, item)


class TestEasyAuthDingTalkManagedUsersAPI(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )
        self.user = create_test_user("api-reader")
        self.url = (
            "/api/v3/sources/oauth/dingtalk-directory/"
            "dingtalk/managed-users/by-manager/CORP/MANAGER/"
        )

    def _login_with_directory_access(self):
        self.user.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource")
        self.user.assign_perms_to_managed_role(
            "authentik_sources_oauth.view_dingtalkdirectoryuser"
        )
        self.client.force_login(self.user)

    def test_success_returns_service_response(self):
        self._login_with_directory_access()
        service_response = {
            "source_slug": "dingtalk",
            "corp_id": "CORP",
            "manager_user_id": "MANAGER",
            "resolver": "dingtalk_manager_chain",
            "resolved_at": "2026-07-02T03:04:05+00:00",
            "stale": False,
            "last_synced_at": "2026-07-02T02:00:00+00:00",
            "diagnostics": {
                "manager_chain_depth": 2,
                "raw": {"cursor": "SECRET_RAW_CONTEXT"},
            },
            "consumer_secret": "LEAKED_SERVICE_SECRET",
            "users": [
                {
                    "source_user_id": "EMP1",
                    "source_identifier": "CORP:EMP1",
                    "authentik_subject": "ak_uid_001",
                    "authentik_subject_type": "user_uid",
                    "authentik_user_active": True,
                    "directory_active": True,
                    "is_deleted": False,
                    "binding_status": "bound",
                    "diagnostics": {
                        "authentik_user_pk": 42,
                        "raw": {"profile": "SECRET_RAW_PROFILE"},
                    },
                    "mobile": "13800000000",
                    "email": "emp1@example.invalid",
                    "union_id": "UNION_ID",
                    "open_id": "OPEN_ID",
                    "raw": {"userid": "EMP1"},
                }
            ],
        }
        expected_response = {
            "source_slug": "dingtalk",
            "corp_id": "CORP",
            "manager_user_id": "MANAGER",
            "resolver": "dingtalk_manager_chain",
            "resolved_at": "2026-07-02T03:04:05+00:00",
            "stale": False,
            "last_synced_at": "2026-07-02T02:00:00+00:00",
            "diagnostics": {
                "manager_chain_depth": 2,
            },
            "users": [
                {
                    "source_user_id": "EMP1",
                    "source_identifier": "CORP:EMP1",
                    "authentik_subject": "ak_uid_001",
                    "authentik_subject_type": "user_uid",
                    "authentik_user_active": True,
                    "directory_active": True,
                    "is_deleted": False,
                    "binding_status": "bound",
                    "diagnostics": {
                        "authentik_user_pk": 42,
                    },
                }
            ],
        }

        with patch(
            "authentik.custom.easyauth.api.dingtalk_managed_users."
            "get_dingtalk_managed_users",
            return_value=service_response,
        ) as get_managed_users:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected_response)
        assert_sensitive_keys_absent(self, response.json())
        get_managed_users.assert_called_once_with(self.source, "CORP", "MANAGER")

    def test_manager_missing_returns_404_code(self):
        self._login_with_directory_access()

        with patch(
            "authentik.custom.easyauth.api.dingtalk_managed_users."
            "get_dingtalk_managed_users",
            side_effect=DingTalkManagerNotFound("missing"),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "manager_not_found")

    def test_disabled_source_returns_404(self):
        self._login_with_directory_access()
        self.source.enabled = False
        self.source.save(update_fields=["enabled"])

        with patch(
            "authentik.custom.easyauth.api.dingtalk_managed_users."
            "get_dingtalk_managed_users",
        ) as get_managed_users:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        get_managed_users.assert_not_called()

    def test_binding_conflict_returns_409(self):
        self._login_with_directory_access()

        with patch(
            "authentik.custom.easyauth.api.dingtalk_managed_users."
            "get_dingtalk_managed_users",
            side_effect=DingTalkBindingConflict("conflict"),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 409)

    def test_without_directory_permission_returns_403(self):
        self.user.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource")
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_sensitive_source_fields_do_not_appear(self):
        self._login_with_directory_access()
        service_response = {
            "source_slug": "dingtalk",
            "corp_id": "CORP",
            "manager_user_id": "MANAGER",
            "resolver": "dingtalk_manager_chain",
            "users": [],
        }

        with patch(
            "authentik.custom.easyauth.api.dingtalk_managed_users."
            "get_dingtalk_managed_users",
            return_value=service_response,
        ):
            response = self.client.get(self.url)

        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("CLIENT_SECRET", body)
        self.assertNotIn("consumer_secret", body)
