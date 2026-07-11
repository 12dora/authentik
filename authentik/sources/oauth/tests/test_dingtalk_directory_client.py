"""DingTalk directory client tests."""

from django.core.cache import cache
from django.test import TestCase
from requests_mock import Mocker

from authentik.sources.oauth.dingtalk.client import DingTalkDirectoryClient
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_DEPARTMENT_LIST_URL,
    DINGTALK_ORG_AUTH_INFO_URL,
    _dingtalk_app_token_cache_key,
    fetch_dingtalk_org_auth_info,
)


class TestDingTalkDirectoryClient(TestCase):
    def setUp(self):
        cache.clear()
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    def test_fetch_departments_normalizes_ids(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                [
                    {
                        "json": {
                            "errcode": 0,
                            "result": [
                                {"dept_id": 2, "name": "Engineering", "parent_id": 1},
                            ],
                        }
                    },
                    {"json": {"errcode": 0, "result": []}},
                ],
            )

            departments = list(DingTalkDirectoryClient(self.source).iter_departments())

        self.assertEqual(
            departments,
            [
                {
                    "dept_id": "2",
                    "name": "Engineering",
                    "parent_dept_id": "1",
                    "raw": {"dept_id": 2, "name": "Engineering", "parent_id": 1},
                }
            ],
        )

    def test_cache_fingerprint_changes_when_secret_rotates(self):
        first = _dingtalk_app_token_cache_key(self.source)
        self.source.consumer_secret = "ROTATED_SECRET"

        self.assertNotEqual(first, _dingtalk_app_token_cache_key(self.source))

    def test_invalid_cached_token_is_refreshed_once(self):
        with Mocker() as mocker:
            mocker.get(
                DINGTALK_APP_ACCESS_TOKEN_URL,
                [
                    {"json": {"access_token": "STALE_TOKEN"}},
                    {"json": {"access_token": "FRESH_TOKEN"}},
                ],
            )
            mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                [
                    {"json": {"errcode": 40014, "errmsg": "invalid token"}},
                    {"json": {"errcode": 0, "result": []}},
                ],
            )

            departments = list(DingTalkDirectoryClient(self.source).iter_departments())

        self.assertEqual(departments, [])
        token_requests = [
            request
            for request in mocker.request_history
            if request.url.startswith(DINGTALK_APP_ACCESS_TOKEN_URL)
        ]
        self.assertEqual(len(token_requests), 2)

    def test_org_discovery_reuses_shared_app_token_cache(self):
        with Mocker() as mocker:
            token = mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={"errcode": 0, "result": {"corpName": "Example"}},
            )

            first = fetch_dingtalk_org_auth_info(self.source, "CORP")
            second = fetch_dingtalk_org_auth_info(self.source, "CORP")

        self.assertEqual(first["label"], "Example")
        self.assertEqual(second["label"], "Example")
        self.assertEqual(token.call_count, 1)
