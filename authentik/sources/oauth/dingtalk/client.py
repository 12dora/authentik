"""DingTalk directory API client."""

from collections.abc import Iterator
from http import HTTPStatus
from typing import Any

from requests import Session

from authentik.lib.utils.http import get_http_session
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_DEPARTMENT_LIST_URL,
    DINGTALK_USER_DETAIL_URL,
    _legacy_error,
    fetch_dingtalk_app_token_cached,
)

DINGTALK_DEPARTMENT_USER_LIST_URL = "https://oapi.dingtalk.com/topapi/v2/user/list"
DINGTALK_MAX_DEPARTMENT_DEPTH = 50
DINGTALK_MAX_DEPARTMENTS = 10000
DINGTALK_PAGE_SIZE = 100
# Hard cap on department-user pages to bound a broken/looping cursor (each page is 100 users).
DINGTALK_MAX_USER_PAGES = 10000
DINGTALK_INVALID_TOKEN_CODES = {40014, 42001}


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
        # Reuse the shared, cross-request cached app token (C3): DingTalk rate-limits gettoken
        # and returns the same token during its validity, so per-corp syncs must not re-fetch.
        self._app_token = fetch_dingtalk_app_token_cached(self.source, self.session)
        return self._app_token

    def _refresh_app_token(self) -> None:
        self._app_token = fetch_dingtalk_app_token_cached(self.source, self.session, force=True)

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            response = self.session.post(
                url,
                params={"access_token": self.app_token},
                json=payload,
            )
            if response.status_code == HTTPStatus.UNAUTHORIZED and attempt == 0:
                self._refresh_app_token()
                continue
            response.raise_for_status()
            data = response.json()
            if data.get("errcode") in DINGTALK_INVALID_TOKEN_CODES and attempt == 0:
                self._refresh_app_token()
                continue
            return data
        raise ValueError("DingTalk app token refresh did not recover the request.")

    def iter_departments(self) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()

        def fetch_children(parent_id: str = "1", depth: int = 0) -> Iterator[dict[str, Any]]:
            if depth > DINGTALK_MAX_DEPARTMENT_DEPTH:
                raise ValueError("DingTalk department traversal depth limit exceeded.")
            data = self._post_json(DINGTALK_DEPARTMENT_LIST_URL, {"dept_id": parent_id})
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
        pages = 0
        while True:
            data = self._post_json(
                DINGTALK_DEPARTMENT_USER_LIST_URL,
                {"dept_id": dept_id, "cursor": cursor, "size": DINGTALK_PAGE_SIZE},
            )
            if error := _legacy_error(data):
                raise ValueError(error)
            result = data.get("result") or {}
            users = result.get("list") or []
            for user in users:
                if isinstance(user, dict):
                    yield user
            if not result.get("has_more"):
                break
            pages += 1
            raw_next = result.get("next_cursor")
            if raw_next is None:
                raw_next = result.get("nextCursor")
            try:
                next_cursor = int(raw_next) if raw_next is not None else None
            except TypeError, ValueError:
                next_cursor = None
            # C5: DingTalk must return a strictly-advancing cursor while has_more is set;
            # a missing/non-advancing cursor (or too many pages) would otherwise re-fetch page 1
            # forever, so abort loudly instead of looping.
            if next_cursor is None or next_cursor <= cursor or pages >= DINGTALK_MAX_USER_PAGES:
                raise ValueError(
                    "DingTalk department user pagination did not advance; aborting to avoid a loop."
                )
            cursor = next_cursor

    def get_user_detail(self, user_id: str) -> dict[str, Any]:
        data = self._post_json(DINGTALK_USER_DETAIL_URL, {"userid": user_id})
        if error := _legacy_error(data):
            raise ValueError(error)
        return data.get("result") or {}
