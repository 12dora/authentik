"""http helpers"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from requests.sessions import PreparedRequest, Session
from structlog.stdlib import get_logger

from authentik import authentik_full_version
from authentik.lib.config import CONFIG

LOGGER = get_logger()
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-acs-dingtalk-access-token",
}
SENSITIVE_QUERY_NAMES = {
    "access_token",
    "appsecret",
    "client_secret",
    "clientsecret",
    "code",
    "refresh_token",
}
REDACTED = "[redacted]"


def authentik_user_agent() -> str:
    """Get a common user agent"""
    return f"authentik@{authentik_full_version()}"


class TimeoutSession(Session):
    """Always set a default HTTP request timeout"""

    def __init__(self, default_timeout=None):
        super().__init__()
        self.timeout = default_timeout

    def send(
        self,
        request,
        *,
        stream=...,
        verify=...,
        proxies=...,
        cert=...,
        timeout=...,
        allow_redirects=...,
        **kwargs,
    ):
        if not timeout and self.timeout:
            timeout = self.timeout
        return super().send(
            request,
            stream=stream,
            verify=verify,
            proxies=proxies,
            cert=cert,
            timeout=timeout,
            allow_redirects=allow_redirects,
            **kwargs,
        )


class DebugSession(TimeoutSession):
    """requests session which logs http requests and responses"""

    def _redact_url(self, url: str | None) -> str | None:
        if not url:
            return url
        parsed = urlsplit(url)
        query = urlencode(
            [
                (key, REDACTED if key.lower() in SENSITIVE_QUERY_NAMES else value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))

    def _redact_headers(self, headers) -> dict[str, str]:
        return {
            key: REDACTED if key.lower() in SENSITIVE_HEADER_NAMES else value
            for key, value in dict(headers or {}).items()
        }

    def send(self, req: PreparedRequest, *args, **kwargs):
        request_id = str(uuid4())
        LOGGER.debug(
            "HTTP request sent",
            uid=request_id,
            url=self._redact_url(req.url),
            method=req.method,
            headers=self._redact_headers(req.headers),
            body=REDACTED if req.body else None,
        )
        resp = super().send(req, *args, **kwargs)
        LOGGER.debug(
            "HTTP response received",
            uid=request_id,
            status=resp.status_code,
            body=REDACTED if resp.content else None,
            headers=self._redact_headers(resp.headers),
        )
        return resp


def get_http_session() -> Session:
    """Get a requests session with common headers"""
    session = TimeoutSession()
    if CONFIG.get_bool("debug") or CONFIG.get("log_level") == "trace":
        session = DebugSession()
    session.headers["User-Agent"] = authentik_user_agent()
    session.timeout = CONFIG.get_optional_int("http_timeout")
    return session
