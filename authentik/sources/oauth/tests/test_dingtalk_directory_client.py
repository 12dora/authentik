"""DingTalk directory client tests."""

from http import HTTPStatus
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from requests_mock import Mocker

from authentik.sources.oauth.dingtalk.client import (
    DINGTALK_DEPARTMENT_USER_LIST_URL,
    DingTalkDirectoryClient,
    DingTalkRequestBudget,
)
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_DEPARTMENT_LIST_URL,
    DINGTALK_ORG_AUTH_INFO_URL,
    DingTalkAppTokenError,
    _dingtalk_app_token_cache_key,
    fetch_dingtalk_app_token_cached,
    fetch_dingtalk_org_auth_info,
)


class TestDingTalkDirectoryClient(TestCase):
    def setUp(self):
        cache.clear()
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            enabled=True,
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

    def test_app_token_rejects_non_object_json(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json=["not", "an", "object"])

            with self.assertRaisesMessage(DingTalkAppTokenError, "request failed"):
                fetch_dingtalk_app_token_cached(self.source)

    def test_app_token_waits_for_existing_lease_and_reuses_cached_token(self):
        key = _dingtalk_app_token_cache_key(self.source)
        cache.add(f"{key}/lease", "1", 30)

        def sleeper(_seconds):
            cache.set(key, "LEASED_TOKEN")

        with patch("authentik.sources.oauth.types.dingtalk.sleep", side_effect=sleeper):
            token = fetch_dingtalk_app_token_cached(self.source)

        self.assertEqual(token, "LEASED_TOKEN")

    def test_forced_app_token_waits_for_existing_lease_until_cache_changes(self):
        key = _dingtalk_app_token_cache_key(self.source)
        cache.set(key, "STALE_TOKEN")
        cache.add(f"{key}/lease", "1", 30)

        def sleeper(_seconds):
            cache.set(key, "FRESH_TOKEN")

        with patch("authentik.sources.oauth.types.dingtalk.sleep", side_effect=sleeper):
            token = fetch_dingtalk_app_token_cached(self.source, force=True)

        self.assertEqual(token, "FRESH_TOKEN")

    def test_department_limits_are_constructor_scoped(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                json={
                    "errcode": 0,
                    "result": [{"dept_id": 2, "name": "Engineering", "parent_id": 1}],
                },
            )

            with self.assertRaisesMessage(ValueError, "department limit"):
                list(DingTalkDirectoryClient(self.source, max_departments=0).iter_departments())

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

    def test_missing_department_result_fails_instead_of_empty_snapshot(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(DINGTALK_DEPARTMENT_LIST_URL, json={"errcode": 0})

            with self.assertRaisesMessage(ValueError, "did not include result"):
                list(DingTalkDirectoryClient(self.source).iter_departments())

    def test_malformed_department_row_fails_instead_of_skipping(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                json={"errcode": 0, "result": [{"name": "Missing id"}]},
            )

            with self.assertRaisesMessage(ValueError, "dept_id"):
                list(DingTalkDirectoryClient(self.source).iter_departments())

    def test_transient_429_retries_after_bounded_retry_after(self):
        sleeps = []
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(
                DINGTALK_DEPARTMENT_USER_LIST_URL,
                [
                    {
                        "status_code": HTTPStatus.TOO_MANY_REQUESTS,
                        "headers": {"Retry-After": "2"},
                        "json": {"errcode": 88, "errmsg": "too many requests"},
                    },
                    {
                        "json": {
                            "errcode": 0,
                            "result": {
                                "list": [{"userid": "USER", "active": True}],
                                "has_more": False,
                            },
                        }
                    },
                ],
            )

            users = list(
                DingTalkDirectoryClient(self.source, sleeper=sleeps.append).iter_department_users(
                    "1"
                )
            )

        self.assertEqual(users, [{"userid": "USER", "active": True}])
        self.assertEqual(sleeps, [2.0])

    def test_request_budget_exhaustion_aborts_before_http(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            post = mocker.post(DINGTALK_DEPARTMENT_LIST_URL, json={"errcode": 0, "result": []})

            with self.assertRaisesMessage(ValueError, "request budget"):
                list(
                    DingTalkDirectoryClient(
                        self.source,
                        request_budget=DingTalkRequestBudget(max_requests=0),
                    ).iter_departments()
                )

        self.assertEqual(post.call_count, 0)

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
