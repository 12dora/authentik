"""DingTalk directory API client."""

from collections.abc import Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from time import sleep
from typing import Any

from requests import Session
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from authentik.lib.utils.http import get_http_session
from authentik.sources.oauth.dingtalk.config import (
    DINGTALK_MAX_DEPARTMENT_DEPTH,
    DINGTALK_MAX_DEPARTMENTS,
)
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_DEPARTMENT_LIST_URL,
    DINGTALK_INVALID_TOKEN_CODES,
    DINGTALK_USER_DETAIL_URL,
    _legacy_error,
    fetch_dingtalk_app_token_cached,
)

DINGTALK_DEPARTMENT_USER_LIST_URL = "https://oapi.dingtalk.com/topapi/v2/user/list"
DINGTALK_PAGE_SIZE = 100
# Hard cap on department-user pages to bound a broken/looping cursor (each page is 100 users).
DINGTALK_MAX_USER_PAGES = 10000
DINGTALK_MAX_REQUESTS_PER_SYNC = 120000
DINGTALK_MAX_REQUEST_ATTEMPTS = 3
DINGTALK_MAX_RETRY_AFTER_SECONDS = 30
DINGTALK_TRANSIENT_STATUSES = {
    HTTPStatus.TOO_MANY_REQUESTS,
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
}


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"DingTalk {context} response was not an object.")
    return value


def _require_list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"DingTalk {context} response was not a list.")
    return value


def _retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError):
            return 0
    return max(0, min(seconds, DINGTALK_MAX_RETRY_AFTER_SECONDS))


class DingTalkRequestBudget:
    """Bound outbound DingTalk calls made by one directory sync."""

    def __init__(self, max_requests: int = DINGTALK_MAX_REQUESTS_PER_SYNC):
        self.max_requests = max_requests
        self.used = 0

    def consume(self) -> None:
        self.used += 1
        if self.used > self.max_requests:
            raise ValueError("DingTalk directory sync request budget exceeded.")


class DingTalkDirectoryClient:
    """Small client for read-only DingTalk directory endpoints."""

    def __init__(
        self,
        source: OAuthSource,
        session: Session | None = None,
        request_budget: DingTalkRequestBudget | None = None,
        sleeper=sleep,
        max_department_depth: int = DINGTALK_MAX_DEPARTMENT_DEPTH,
        max_departments: int = DINGTALK_MAX_DEPARTMENTS,
    ):
        self.source = source
        self.session = session or get_http_session()
        self.request_budget = request_budget or DingTalkRequestBudget()
        self.sleeper = sleeper
        self.max_department_depth = max_department_depth
        self.max_departments = max_departments
        self._app_token = ""

    @property
    def app_token(self) -> str:
        if self._app_token:
            return self._app_token
        # Reuse the shared, cross-request cached app token: DingTalk rate-limits gettoken
        # and returns the same token during its validity, so per-corp syncs must not re-fetch.
        self._app_token = fetch_dingtalk_app_token_cached(self.source, self.session)
        return self._app_token

    def _refresh_app_token(self) -> None:
        self._app_token = fetch_dingtalk_app_token_cached(self.source, self.session, force=True)

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(DINGTALK_MAX_REQUEST_ATTEMPTS):
            self.request_budget.consume()
            try:
                response = self.session.post(
                    url,
                    params={"access_token": self.app_token},
                    json=payload,
                )
            except (RequestsConnectionError, Timeout):
                if attempt == DINGTALK_MAX_REQUEST_ATTEMPTS - 1:
                    raise
                continue
            if response.status_code == HTTPStatus.UNAUTHORIZED and attempt == 0:
                self._refresh_app_token()
                continue
            if response.status_code in DINGTALK_TRANSIENT_STATUSES:
                if attempt == DINGTALK_MAX_REQUEST_ATTEMPTS - 1:
                    response.raise_for_status()
                self.sleeper(_retry_after_seconds(response.headers.get("Retry-After")))
                continue
            response.raise_for_status()
            data = _require_mapping(response.json(), "API")
            if data.get("errcode") in DINGTALK_INVALID_TOKEN_CODES and attempt == 0:
                self._refresh_app_token()
                continue
            return data
        raise ValueError("DingTalk request retry budget was exhausted.")

    def iter_departments(self) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()

        def fetch_children(parent_id: str = "1", depth: int = 0) -> Iterator[dict[str, Any]]:
            if depth > self.max_department_depth:
                raise ValueError("DingTalk department traversal depth limit exceeded.")
            data = self._post_json(DINGTALK_DEPARTMENT_LIST_URL, {"dept_id": parent_id})
            if error := _legacy_error(data):
                raise ValueError(error)
            if "result" not in data:
                raise ValueError("DingTalk department response did not include result.")
            result = data["result"]
            if isinstance(result, dict):
                if "dept_id_list" in result:
                    result = result["dept_id_list"]
                elif "departments" in result:
                    result = result["departments"]
                else:
                    raise ValueError("DingTalk department response did not include departments.")
            result = _require_list(result, "department")
            for department in result:
                if not isinstance(department, dict):
                    raise ValueError("DingTalk department row was not an object.")
                dept_id = department.get("dept_id") or department.get("deptId")
                if dept_id is None:
                    raise ValueError("DingTalk department row did not include dept_id.")
                dept_id = str(dept_id)
                if dept_id in seen:
                    continue
                if len(seen) >= self.max_departments:
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
            if "result" not in data:
                raise ValueError("DingTalk department user response did not include result.")
            result = _require_mapping(data["result"], "department user result")
            if "list" not in result:
                raise ValueError("DingTalk department user response did not include result.list.")
            users = _require_list(result["list"], "department user list")
            for user in users:
                if not isinstance(user, dict):
                    raise ValueError("DingTalk department user row was not an object.")
                yield user
            if not result.get("has_more"):
                break
            pages += 1
            raw_next = result.get("next_cursor")
            if raw_next is None:
                raw_next = result.get("nextCursor")
            try:
                next_cursor = int(raw_next) if raw_next is not None else None
            except (TypeError, ValueError):
                next_cursor = None
            # DingTalk must return a strictly-advancing cursor while has_more is set;
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
        if "result" not in data:
            raise ValueError("DingTalk user detail response did not include result.")
        return _require_mapping(data["result"], "user detail result")
