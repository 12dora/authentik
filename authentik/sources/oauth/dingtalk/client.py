"""DingTalk directory API client."""

from collections.abc import Iterator
from typing import Any

from requests import Session

from authentik.lib.utils.http import get_http_session
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_DEPARTMENT_LIST_URL,
    DINGTALK_USER_DETAIL_URL,
    _legacy_error,
)

DINGTALK_DEPARTMENT_USER_LIST_URL = "https://oapi.dingtalk.com/topapi/v2/user/list"
DINGTALK_MAX_DEPARTMENT_DEPTH = 50
DINGTALK_MAX_DEPARTMENTS = 10000
DINGTALK_PAGE_SIZE = 100


class DingTalkDirectoryClient:
    """Small client for read-only DingTalk directory endpoints."""

    def __init__(self, source: OAuthSource, session: Session | None = None):
        self.source = source
        self.session = session or get_http_session()
        self._app_token = ""

    @property
    def app_token(self) -> str:
        if self._app_token:
            return self._app_token
        response = self.session.get(
            DINGTALK_APP_ACCESS_TOKEN_URL,
            params={"appkey": self.source.consumer_key, "appsecret": self.source.consumer_secret},
        )
        response.raise_for_status()
        data = response.json()
        error = _legacy_error(data)
        token = data.get("access_token") or data.get("accessToken")
        if error or not token:
            raise ValueError(error or "DingTalk app token response did not include a token.")
        self._app_token = token
        return token

    def iter_departments(self) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()

        def fetch_children(parent_id: str = "1", depth: int = 0) -> Iterator[dict[str, Any]]:
            if depth > DINGTALK_MAX_DEPARTMENT_DEPTH:
                raise ValueError("DingTalk department traversal depth limit exceeded.")
            response = self.session.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                params={"access_token": self.app_token},
                json={"dept_id": parent_id},
            )
            response.raise_for_status()
            data = response.json()
            if error := _legacy_error(data):
                raise ValueError(error)
            result = data.get("result") or []
            if isinstance(result, dict):
                result = result.get("dept_id_list") or result.get("departments") or []
            if not isinstance(result, list | tuple | set):
                return
            for department in result:
                if not isinstance(department, dict):
                    continue
                dept_id = department.get("dept_id") or department.get("deptId")
                if dept_id is None:
                    continue
                dept_id = str(dept_id)
                if dept_id in seen:
                    continue
                if len(seen) >= DINGTALK_MAX_DEPARTMENTS:
                    raise ValueError("DingTalk department traversal department limit exceeded.")
                seen.add(dept_id)
                normalized = {
                    "dept_id": dept_id,
                    "name": department.get("name") or department.get("dept_name") or "",
                    "parent_dept_id": str(
                        department.get("parent_id") or department.get("parentId") or parent_id
                    ),
                    "raw": department,
                }
                yield normalized
                yield from fetch_children(dept_id, depth + 1)

        yield from fetch_children()

    def iter_department_users(self, dept_id: str) -> Iterator[dict[str, Any]]:
        cursor = 0
        while True:
            response = self.session.post(
                DINGTALK_DEPARTMENT_USER_LIST_URL,
                params={"access_token": self.app_token},
                json={"dept_id": dept_id, "cursor": cursor, "size": DINGTALK_PAGE_SIZE},
            )
            response.raise_for_status()
            data = response.json()
            if error := _legacy_error(data):
                raise ValueError(error)
            result = data.get("result") or {}
            users = result.get("list") or []
            for user in users:
                if isinstance(user, dict):
                    yield user
            if not result.get("has_more"):
                break
            cursor = result.get("next_cursor") or result.get("nextCursor") or 0

    def get_user_detail(self, user_id: str) -> dict[str, Any]:
        response = self.session.post(
            DINGTALK_USER_DETAIL_URL,
            params={"access_token": self.app_token},
            json={"userid": user_id},
        )
        response.raise_for_status()
        data = response.json()
        if error := _legacy_error(data):
            raise ValueError(error)
        return data.get("result") or {}
