"""DingTalk directory client tests."""

from django.test import TestCase
from requests_mock import Mocker

from authentik.sources.oauth.dingtalk.client import DingTalkDirectoryClient
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_DEPARTMENT_LIST_URL,
)


class TestDingTalkDirectoryClient(TestCase):
    def setUp(self):
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
