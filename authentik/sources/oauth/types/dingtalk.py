"""DingTalk OAuth Views"""

from hashlib import sha256
from json import dumps, loads
from secrets import token_urlsafe
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.timezone import now
from requests import Session
from requests.exceptions import JSONDecodeError, RequestException

from authentik.events.models import Event, EventAction
from authentik.flows.models import FlowStageBinding
from authentik.lib.utils.http import get_http_session
from authentik.policies.expression.models import ExpressionPolicy
from authentik.policies.models import PolicyBinding
from authentik.sources.oauth.clients.oauth2 import OAuth2Client
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.registry import SourceType, registry
from authentik.sources.oauth.views.callback import OAuthCallback
from authentik.sources.oauth.views.redirect import OAuthRedirect

DINGTALK_AUTHORIZE_URL = "https://login.dingtalk.com/oauth2/auth"
DINGTALK_ACCESS_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"  # nosec
DINGTALK_PROFILE_URL = "https://api.dingtalk.com/v1.0/contact/users/me"
DINGTALK_ORG_AUTH_INFO_URL = "https://api.dingtalk.com/v1.0/contact/organizations/authInfos"

DINGTALK_APP_ACCESS_TOKEN_URL = "https://oapi.dingtalk.com/gettoken"  # nosec
DINGTALK_GET_BY_UNION_ID_URL = "https://oapi.dingtalk.com/topapi/user/getbyunionid"
DINGTALK_DEPARTMENT_LIST_URL = "https://oapi.dingtalk.com/topapi/v2/department/listsub"
DINGTALK_USER_DETAIL_URL = "https://oapi.dingtalk.com/topapi/v2/user/get"
DINGTALK_ALLOWLIST_MARKER = "# authentik-managed-dingtalk-allowlist"
DINGTALK_ALLOWLIST_SESSION_KEY = "authentik/sources/oauth/dingtalk/allowlist"
DINGTALK_ALLOWLIST_PLAN_CONTEXT = "authentik/sources/oauth/dingtalk/allowlist/pending"
DINGTALK_ALLOWLIST_SCOPES = ["openid", "corpid", "Contact.User.Read"]
DINGTALK_ALLOWLIST_STATE_SALT = "authentik.sources.oauth.dingtalk.allowlist"
DINGTALK_MAX_DEPARTMENT_DEPTH = 50
DINGTALK_MAX_DEPARTMENTS = 10000
DINGTALK_DEPARTMENT_CORP_UNAVAILABLE = (
    "DingTalk departments can only be loaded for a company authorized by this DingTalk "
    "application. Edit the company label manually, or bind/authorize this company in the "
    "DingTalk developer console before loading departments."
)


class DingTalkDepartmentCorpUnavailable(ValueError):
    """Raised when the current DingTalk app token cannot verify the requested corp."""


def _legacy_error(data: dict[str, Any]) -> str:
    errcode = data.get("errcode")
    if errcode in (None, 0):
        return ""
    return str(data.get("errmsg") or data.get("message") or data.get("error") or errcode)


def _normalize_id_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple | set):
        return []
    return sorted({str(item) for item in value if item is not None})


def _extract_dingtalk_corp_label(data: dict[str, Any]) -> str:
    label_keys = (
        "corpName",
        "corp_name",
        "contactName",
        "contact_name",
        "enterpriseName",
        "enterprise_name",
        "organizationName",
        "organization_name",
        "orgName",
        "org_name",
        "name",
    )

    def find_label(value: Any) -> str:
        if isinstance(value, dict):
            for key in label_keys:
                if label := value.get(key):
                    return str(label)
            for nested in value.values():
                if label := find_label(nested):
                    return label
        if isinstance(value, list | tuple):
            for nested in value:
                if label := find_label(nested):
                    return label
        return ""

    return find_label(
        [
            data,
            data.get("authOrgInfo"),
            data.get("auth_org_info"),
            data.get("authInfos"),
            data.get("auth_infos"),
            data.get("auth_corp_info"),
            data.get("corpInfo"),
            data.get("corp_info"),
            data.get("result"),
        ]
    )


def _fetch_dingtalk_app_token(source: OAuthSource, session: Session) -> str:
    token_response = session.get(
        DINGTALK_APP_ACCESS_TOKEN_URL,
        params={"appkey": source.consumer_key, "appsecret": source.consumer_secret},
    )
    token_response.raise_for_status()
    token_data = token_response.json()
    error = _legacy_error(token_data)
    app_token = token_data.get("access_token") or token_data.get("accessToken")
    if error or not app_token:
        raise ValueError(error or "DingTalk app token response did not include a token.")
    return str(app_token)


def normalize_dingtalk_allowlist_config(config: dict[str, Any]) -> dict[str, Any]:
    companies = []
    for item in config.get("companies") or []:
        if not isinstance(item, dict):
            continue
        corp_id = item.get("corp_id") or item.get("corpId")
        if not corp_id:
            continue
        companies.append(
            {
                "allow_all": bool(item.get("allow_all", False)),
                "corp_id": str(corp_id),
                "dept_ids": _normalize_id_list(
                    item.get("dept_ids") or item.get("dept_id_list") or item.get("departments")
                ),
                "label": str(item.get("label") or item.get("name") or ""),
            }
        )
    return {"companies": companies}


def dingtalk_allowlist_config_hash(config: dict[str, Any]) -> str:
    """Return a stable hash for a normalized DingTalk allowlist config."""
    return sha256(dingtalk_allowlist_config_version(config).encode("utf-8")).hexdigest()


def dingtalk_allowlist_config_version(config: dict[str, Any]) -> str:
    """Return a stable serialized version for a normalized DingTalk allowlist config."""
    return dumps(
        normalize_dingtalk_allowlist_config(config),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_dingtalk_allowlist_policy(expression: str) -> dict[str, Any] | None:
    if DINGTALK_ALLOWLIST_MARKER not in expression:
        return None
    for raw_line in expression.splitlines():
        line = raw_line.strip()
        if not line.startswith("# config:"):
            continue
        try:
            config = loads(line.removeprefix("# config:").strip())
            return normalize_dingtalk_allowlist_config(config)
        except ValueError:
            return None
    return {"companies": []}


def evaluate_dingtalk_allowlist(config: dict[str, Any], userinfo: dict[str, Any]) -> bool:
    corp_id = userinfo.get("corp_id") or userinfo.get("corpId")
    dept_ids = _normalize_id_list(userinfo.get("dept_id_list") or userinfo.get("deptIdList"))
    if not corp_id:
        return False
    for company in normalize_dingtalk_allowlist_config(config).get("companies", []):
        if company["corp_id"] != str(corp_id):
            continue
        if company["allow_all"]:
            return True
        return bool(dept_ids and set(dept_ids).intersection(company["dept_ids"]))
    return False


def _matching_dingtalk_allowlist_company(
    config: dict[str, Any], userinfo: dict[str, Any]
) -> dict[str, Any] | None:
    corp_id = userinfo.get("corp_id") or userinfo.get("corpId")
    dept_ids = _normalize_id_list(userinfo.get("dept_id_list") or userinfo.get("deptIdList"))
    if not corp_id:
        return None
    for company in normalize_dingtalk_allowlist_config(config).get("companies", []):
        if company["corp_id"] != str(corp_id):
            continue
        if company["allow_all"] or (dept_ids and set(dept_ids).intersection(company["dept_ids"])):
            return company
        return None
    return None


def build_dingtalk_allowlist_session_marker(
    config: dict[str, Any],
    userinfo: dict[str, Any],
    source: OAuthSource,
    identifier: str,
) -> dict[str, Any] | None:
    """Build current-login DingTalk allowlist evidence for downstream application policies."""
    matched = _matching_dingtalk_allowlist_company(config, userinfo)
    if not matched:
        return None
    corp_id = userinfo.get("corp_id") or userinfo.get("corpId")
    dept_ids = _normalize_id_list(userinfo.get("dept_id_list") or userinfo.get("deptIdList"))
    return {
        "source_slug": source.slug,
        "source_pk": str(source.pk),
        "source_identifier": str(identifier),
        "corp_id": str(corp_id),
        "dept_ids": dept_ids,
        "dingtalk_user_id": userinfo.get("userid") or userinfo.get("userId"),
        "dingtalk_union_id": userinfo.get("unionId"),
        "config_hash": dingtalk_allowlist_config_hash(config),
        "config_version": dingtalk_allowlist_config_version(config),
        "checked_at": now().isoformat(),
    }


def inject_dingtalk_allowlist_policy_context(policy_request) -> None:
    """Expose current-session DingTalk allowlist evidence to application policies."""
    http_request = getattr(policy_request, "http_request", None)
    if not http_request or not hasattr(http_request, "session"):
        return
    marker = http_request.session.get(DINGTALK_ALLOWLIST_SESSION_KEY)
    if not isinstance(marker, dict):
        return
    marker_user_pk = marker.get("user_pk")
    if marker_user_pk is not None and str(marker_user_pk) != str(policy_request.user.pk):
        return
    policy_request.context[DINGTALK_ALLOWLIST_SESSION_KEY] = marker


def render_dingtalk_allowlist_policy(config: dict[str, Any]) -> str:
    normalized = normalize_dingtalk_allowlist_config(config)
    config_json = dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    config_python = repr(normalized)
    config_hash = dingtalk_allowlist_config_hash(normalized)
    return f'''{DINGTALK_ALLOWLIST_MARKER}
# config: {config_json}
expected_source_slug = None
userinfo = request.context.get("oauth_userinfo") or {{}}
corp_id = userinfo.get("corp_id") or userinfo.get("corpId")
dept_values = userinfo.get("dept_id_list") or userinfo.get("deptIdList") or []
if not isinstance(dept_values, (list, tuple, set)):
    dept_ids = []
else:
    dept_ids = sorted({{str(item) for item in dept_values if item is not None}})
if not corp_id:
    if request.obj.__class__.__name__ != "Application":
        return True
    marker = request.context.get("{DINGTALK_ALLOWLIST_SESSION_KEY}") or {{}}
    if not marker:
        ak_message("钉钉登录失败：请通过允许的钉钉组织登录后访问此应用。")
        return False
    marker_current = (
        marker.get("config_hash") == "{config_hash}"
        or marker.get("config_version") == {config_json!r}
    )
    if not marker_current:
        ak_message("钉钉登录失败：当前白名单状态已更新，请重新通过钉钉登录。")
        return False
    corp_id = marker.get("corp_id")
    dept_ids = marker.get("dept_ids") or []
    if not isinstance(dept_ids, (list, tuple, set)):
        dept_ids = []
    else:
        dept_ids = sorted({{str(item) for item in dept_ids if item is not None}})
    if not corp_id:
        ak_message("钉钉登录失败：当前企业未被允许，请联系管理员。")
        return False
config = {config_python}
matched = None
for company in config.get("companies", []):
    if company.get("corp_id") == str(corp_id):
        matched = company
        break
if not matched:
    ak_message("钉钉登录失败：当前企业未被允许，请联系管理员。")
    return False
if matched.get("allow_all"):
    return True
if set(dept_ids).intersection(matched.get("dept_ids") or []):
    return True
ak_message("钉钉登录失败：当前部门未被允许，请联系管理员。")
return False
'''


def get_dingtalk_allowlist_binding(
    source: OAuthSource, enabled_only: bool = True
) -> tuple[PolicyBinding | None, ExpressionPolicy | None, dict[str, Any] | None]:
    targets = [source.pbm_uuid]
    flows = [source.authentication_flow, source.enrollment_flow]
    for flow in [flow for flow in flows if flow]:
        targets.append(flow.pbm_uuid)
        targets.extend(
            FlowStageBinding.objects.filter(target=flow).values_list("pbm_uuid", flat=True)
        )
    bindings = PolicyBinding.objects.filter(policy__isnull=False, target_id__in=targets).order_by(
        "order", "pk"
    )
    if enabled_only:
        bindings = bindings.filter(enabled=True)
    for binding in bindings:
        policy = binding.policy
        if not isinstance(policy, ExpressionPolicy):
            policy = ExpressionPolicy.objects.filter(pk=policy.pk).first()
        if not policy:
            continue
        config = parse_dingtalk_allowlist_policy(policy.expression)
        if config is not None:
            return binding, policy, config
    return None, None, None


def create_dingtalk_discovery_state(request: HttpRequest, source: OAuthSource) -> str:
    state = signing.dumps(
        {"source_slug": source.slug, "nonce": token_urlsafe(24)},
        salt=DINGTALK_ALLOWLIST_STATE_SALT,
    )
    request.session[f"dingtalk-allowlist-state:{state}"] = True
    return state


def _consume_dingtalk_discovery_state(request: HttpRequest, source: OAuthSource) -> str:
    state = request.GET.get("state", "")
    try:
        data = signing.loads(state, salt=DINGTALK_ALLOWLIST_STATE_SALT, max_age=600)
    except signing.SignatureExpired as exc:
        raise ValueError(str(exc)) from exc
    except signing.BadSignature as exc:
        raise ValueError("Invalid DingTalk discovery state.") from exc
    if data.get("source_slug") != source.slug:
        raise ValueError("DingTalk discovery state source mismatch.")
    session_key = f"dingtalk-allowlist-state:{state}"
    if not request.session.pop(session_key, False):
        raise ValueError("DingTalk discovery state has already been used.")
    request.session.modified = True
    return state


def is_dingtalk_discovery_state(source: OAuthSource, state: str) -> bool:
    try:
        data = signing.loads(state, salt=DINGTALK_ALLOWLIST_STATE_SALT)
    except signing.BadSignature:
        return False
    return data.get("source_slug") == source.slug


def dingtalk_oauth_callback_url(request: HttpRequest, source: OAuthSource) -> str:
    return request.build_absolute_uri(
        reverse(
            "authentik_sources_oauth:oauth-client-callback",
            kwargs={"source_slug": source.slug},
        )
    )


def _fetch_dingtalk_user_profile(
    source: OAuthSource, code: str, session: Session
) -> dict[str, Any]:
    token_response = session.post(
        DINGTALK_ACCESS_TOKEN_URL,
        json={
            "clientId": source.consumer_key,
            "clientSecret": source.consumer_secret,
            "code": code,
            "grantType": "authorization_code",
        },
    )
    token_response.raise_for_status()
    token = token_response.json()
    access_token = token.get("accessToken") or token.get("access_token")
    if not access_token:
        raise ValueError("DingTalk token response did not include an access token.")
    profile_response = session.get(
        DINGTALK_PROFILE_URL,
        headers={"x-acs-dingtalk-access-token": access_token},
    )
    profile_response.raise_for_status()
    profile = profile_response.json()
    if corp_id := token.get("corpId") or token.get("corp_id"):
        profile.setdefault("corpId", corp_id)
        profile.setdefault("corp_id", corp_id)
        try:
            org_info = fetch_dingtalk_org_auth_info(source, str(corp_id), session=session)
        except (RequestException, ValueError):
            org_info = {}
        if label := org_info.get("label"):
            profile.setdefault("label", label)
            profile.setdefault("corpName", label)
            profile.setdefault("corp_name", label)
    return profile


def _discovery_response(payload: dict[str, Any]) -> HttpResponse:
    body = dumps(payload, ensure_ascii=False)
    return HttpResponse(
        "<!doctype html><script>"
        f"window.opener && window.opener.postMessage({body}, window.location.origin);"
        "window.close();"
        "</script>"
    )


def handle_dingtalk_discovery_callback(request: HttpRequest, source: OAuthSource) -> HttpResponse:
    try:
        _consume_dingtalk_discovery_state(request, source)
        code = request.GET.get("authCode") or request.GET.get("code")
        if not code:
            raise ValueError("DingTalk discovery callback did not include a code.")
        profile = _fetch_dingtalk_user_profile(source, code, get_http_session())
    except (RequestException, ValueError) as exc:
        return _discovery_response({"ok": False, "error": str(exc)})
    return _discovery_response({"ok": True, "profile": profile, **profile})


def fetch_dingtalk_org_auth_info(
    source: OAuthSource,
    corp_id: str,
    session: Session | None = None,
) -> dict[str, Any]:
    session = session or get_http_session()
    app_token = _fetch_dingtalk_app_token(source, session)
    response = session.get(
        DINGTALK_ORG_AUTH_INFO_URL,
        params={"targetCorpId": corp_id},
        headers={"x-acs-dingtalk-access-token": app_token},
    )
    response.raise_for_status()
    data = response.json()
    if error := _legacy_error(data):
        raise ValueError(error)
    return {
        "corp_id": corp_id,
        "label": _extract_dingtalk_corp_label(data),
        "raw": data,
    }


def fetch_dingtalk_departments(source: OAuthSource, corp_id: str) -> dict[str, Any]:
    from authentik.sources.oauth.dingtalk import client as dingtalk_client

    try:
        org_info = fetch_dingtalk_org_auth_info(source, corp_id)
    except (RequestException, ValueError) as exc:
        raise DingTalkDepartmentCorpUnavailable(DINGTALK_DEPARTMENT_CORP_UNAVAILABLE) from exc

    dingtalk_client.DINGTALK_MAX_DEPARTMENT_DEPTH = DINGTALK_MAX_DEPARTMENT_DEPTH
    dingtalk_client.DINGTALK_MAX_DEPARTMENTS = DINGTALK_MAX_DEPARTMENTS

    departments = []
    for department in dingtalk_client.DingTalkDirectoryClient(source).iter_departments():
        departments.append(
            {
                "dept_id": department["dept_id"],
                "name": department["name"],
                "parent_id": department["parent_dept_id"],
            }
        )
    return {"corp_id": corp_id, "label": org_info.get("label", ""), "departments": departments}


class DingTalkOAuth2Client(OAuth2Client):
    """OAuth2 client for DingTalk's non-standard website login flow."""

    def get_redirect_url(self, parameters=None):
        """Build DingTalk's redirect URL without re-ordering scope values."""
        authorization_url = self.source.source_type.authorization_url or ""
        if authorization_url == "":
            Event.new(
                EventAction.CONFIGURATION_ERROR,
                source=self.source,
                message="Source has an empty authorization URL.",
            ).save()
        parsed_url = urlparse(authorization_url)
        parsed_args = parse_qs(parsed_url.query)
        args = self.get_redirect_args()
        args.update(parameters or {})
        args.update(parsed_args)
        scope = args.get("scope", [])
        if isinstance(scope, str):
            ordered_scope = scope
        else:
            ordered_scope = " ".join(dict.fromkeys(scope))
        args["scope"] = ordered_scope
        params = urlencode(args, quote_via=quote, doseq=True)
        self.logger.info("redirect args", **args)
        return urlunparse(parsed_url._replace(query=params))

    def get_access_token(self, **request_kwargs) -> dict[str, Any] | None:
        """Fetch and normalize DingTalk's non-standard token response."""
        if not self.check_application_state():
            self.logger.warning("Application state check failed.")
            return {"error": "State check failed."}

        code = self.get_request_arg("authCode", None) or self.get_request_arg("code", None)
        if not code:
            error = self.get_request_arg("error", None)
            error_desc = self.get_request_arg("error_description", None)
            return {"error": error_desc or error or "No token received."}

        data = {}
        try:
            response = self.do_request(
                "post",
                self.source.source_type.access_token_url,
                json={
                    "clientId": self.get_client_id(),
                    "clientSecret": self.get_client_secret(),
                    "code": code,
                    "grantType": "authorization_code",
                },
                headers=self._default_headers,
                **request_kwargs,
            )
            try:
                data = response.json()
            except JSONDecodeError as exc:
                response.raise_for_status()
                self.logger.warning("Unable to parse dingtalk token", exc=exc)
                return {"error": "DingTalk token exchange failed."}
            response.raise_for_status()
        except RequestException as exc:
            self.logger.warning("Unable to fetch dingtalk token", exc=exc)
            return {"error": self._get_error(data) or "DingTalk token exchange failed."}

        error = self._get_error(data)
        if error:
            self.logger.warning("Unable to fetch dingtalk token", error=error)
            return {"error": error}

        token = {
            "access_token": data.get("accessToken"),
            "refresh_token": data.get("refreshToken"),
            "expires_in": data.get("expireIn"),
            "token_type": data.get("tokenType", "Bearer"),
            "corp_id": data.get("corpId") or data.get("corp_id"),
        }
        return {key: value for key, value in token.items() if value is not None}

    def get_profile_info(self, token: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch DingTalk profile and enrich with directory data when available."""
        try:
            response = self.do_request(
                "get",
                self.source.source_type.profile_url,
                headers={"x-acs-dingtalk-access-token": token["access_token"]},
            )
            response.raise_for_status()
        except RequestException as exc:
            self.logger.warning("Unable to fetch dingtalk userinfo", exc=exc)
            return None

        profile = response.json()
        if corp_id := token.get("corp_id"):
            profile.setdefault("corpId", corp_id)
        self.dingtalk_raw_profile = profile.copy()
        union_id = profile.get("unionId")
        if not union_id:
            return profile

        detail = self._get_enhanced_profile(union_id)
        if detail:
            profile.update(detail)
        return profile

    def _get_enhanced_profile(self, union_id: str) -> dict[str, Any]:
        """Fetch optional DingTalk directory fields, returning an empty dict on failure."""
        try:
            app_token_response = self.do_request(
                "get",
                DINGTALK_APP_ACCESS_TOKEN_URL,
                params={
                    "appkey": self.get_client_id(),
                    "appsecret": self.get_client_secret(),
                },
            )
            app_token_response.raise_for_status()
            app_token_data = app_token_response.json()
            app_token = app_token_data.get("access_token") or app_token_data.get("accessToken")
            if not app_token or self._has_legacy_error(app_token_data):
                self.logger.warning("Unable to fetch dingtalk app token", response=app_token_data)
                return {}

            user_id_response = self.do_request(
                "post",
                DINGTALK_GET_BY_UNION_ID_URL,
                params={"access_token": app_token},
                json={"unionid": union_id},
            )
            user_id_response.raise_for_status()
            user_id_data = user_id_response.json()
            if self._has_legacy_error(user_id_data):
                self.logger.warning("Unable to fetch dingtalk user id", response=user_id_data)
                return {}
            user_id = user_id_data.get("result", {}).get("userid")
            if not user_id:
                return {}

            detail_response = self.do_request(
                "post",
                DINGTALK_USER_DETAIL_URL,
                params={"access_token": app_token},
                json={"userid": user_id},
            )
            detail_response.raise_for_status()
            detail_data = detail_response.json()
            if self._has_legacy_error(detail_data):
                self.logger.warning("Unable to fetch dingtalk user detail", response=detail_data)
                return {}
        except (JSONDecodeError, RequestException) as exc:
            response = getattr(exc, "response", None)
            self.logger.warning(
                "Unable to fetch dingtalk enhanced userinfo",
                error=exc.__class__.__name__,
                status_code=getattr(response, "status_code", None),
            )
            return {}

        detail = detail_data.get("result", {})
        if "userid" not in detail:
            detail["userid"] = user_id
        return detail

    def _get_error(self, data: dict[str, Any]) -> str | None:
        return (
            data.get("error_description")
            or data.get("errorCode")
            or data.get("error_code")
            or data.get("code")
            or data.get("message")
        )

    def _has_legacy_error(self, data: dict[str, Any]) -> bool:
        errcode = data.get("errcode")
        return errcode not in (None, 0)


class DingTalkOAuthRedirect(OAuthRedirect):
    """DingTalk OAuth2 Redirect"""

    client_class = DingTalkOAuth2Client

    def get_additional_parameters(self, source: OAuthSource):  # pragma: no cover
        return {
            "scope": ["openid", "corpid", "Contact.User.Read"],
            "prompt": "consent",
        }


class DingTalkOAuth2Callback(OAuthCallback):
    """DingTalk OAuth2 Callback"""

    client_class = DingTalkOAuth2Client

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        slug = kwargs.get("source_slug", "")
        source = OAuthSource.objects.filter(slug=slug, enabled=True).first()
        state = request.GET.get("state", "")
        if source and state and is_dingtalk_discovery_state(source, state):
            return handle_dingtalk_discovery_callback(request, source)
        return super().dispatch(request, *args, **kwargs)

    def get_user_id(self, info: dict[str, Any]) -> str | None:
        user_id = info.get("userid") or info.get("userId")
        corp_id = info.get("corpId") or info.get("corp_id")
        if corp_id and user_id:
            return f"{corp_id}:{user_id}"
        return user_id or info.get("unionId") or info.get("openId")


@registry.register()
class DingTalkType(SourceType):
    """DingTalk Type definition"""

    callback_view = DingTalkOAuth2Callback
    redirect_view = DingTalkOAuthRedirect
    verbose_name = "DingTalk"
    name = "dingtalk"

    authorization_url = DINGTALK_AUTHORIZE_URL
    access_token_url = DINGTALK_ACCESS_TOKEN_URL
    profile_url = DINGTALK_PROFILE_URL
    urls_customizable = False

    def get_base_user_properties(
        self, source: OAuthSource, info: dict[str, Any], **kwargs
    ) -> dict[str, Any]:
        client = kwargs.get("client")
        raw_profile = getattr(client, "dingtalk_raw_profile", info)
        user_id = info.get("userid") or info.get("userId")
        corp_id = info.get("corpId") or info.get("corp_id")
        username = (
            info.get("name") or info.get("nick") or DingTalkOAuth2Callback().get_user_id(info)
        )
        dingtalk = {
            "union_id": info.get("unionId"),
            "open_id": info.get("openId"),
            "user_id": user_id,
            "corp_id": corp_id,
            "nick": info.get("nick"),
            "name": info.get("name"),
            "avatar": info.get("avatar") or info.get("avatarUrl"),
            "title": info.get("title"),
            "mobile": info.get("mobile"),
            "state_code": info.get("stateCode"),
            "dept_id_list": info.get("dept_id_list"),
            "job_number": info.get("job_number"),
            "role_list": info.get("role_list"),
            "raw_profile": raw_profile,
        }
        return {
            "username": username,
            "email": info.get("email"),
            "name": info.get("name") or info.get("nick"),
            "attributes": {
                "dingtalk": dingtalk,
            },
        }
