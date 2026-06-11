"""DingTalk allowlist discovery API."""

from urllib.parse import quote, urlencode

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View
from requests.exceptions import RequestException
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_ALLOWLIST_MARKER,
    DINGTALK_ALLOWLIST_SCOPES,
    DINGTALK_AUTHORIZE_URL,
    create_dingtalk_discovery_state,
    dingtalk_oauth_callback_url,
    evaluate_dingtalk_allowlist,
    fetch_dingtalk_departments,
    get_dingtalk_allowlist_binding,
    handle_dingtalk_discovery_callback,
    parse_dingtalk_allowlist_policy,
    render_dingtalk_allowlist_policy,
)

__all__ = [
    "DINGTALK_ALLOWLIST_MARKER",
    "evaluate_dingtalk_allowlist",
    "parse_dingtalk_allowlist_policy",
    "render_dingtalk_allowlist_policy",
]

DINGTALK_ALLOWLIST_EXTERNAL_ERROR = "Could not fetch DingTalk departments."


def get_dingtalk_source(source_slug: str) -> OAuthSource:
    """Get an enabled DingTalk OAuth source by slug."""
    source = get_object_or_404(OAuthSource, slug=source_slug)
    if source.provider_type != "dingtalk":
        raise Http404("Source is not a DingTalk OAuth source.")
    return source


class CanViewDingTalkSource(BasePermission):
    """Require RBAC read access to the DingTalk source for server-side credential endpoints."""

    def has_permission(self, request: Request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        try:
            source = get_dingtalk_source(view.kwargs.get("source_slug", ""))
        except Http404:
            return False
        view.dingtalk_source = source
        return bool(
            request.user.has_perm("authentik_sources_oauth.view_oauthsource")
            or request.user.has_perm("authentik_sources_oauth.view_oauthsource", source)
        )


def get_dingtalk_view_source(view, source_slug: str) -> OAuthSource:
    """Reuse source loaded by the permission check when available."""
    return getattr(view, "dingtalk_source", None) or get_dingtalk_source(source_slug)


def dingtalk_allowlist_callback_url(request: HttpRequest | Request, source: OAuthSource) -> str:
    """Build the DingTalk callback URL used for allowlist discovery."""
    return dingtalk_oauth_callback_url(request, source)


def build_dingtalk_discovery_authorization_url(
    request: HttpRequest | Request,
    source: OAuthSource,
    state: str,
) -> str:
    """Build DingTalk authorization URL for allowlist discovery only."""
    params = {
        "client_id": source.consumer_key,
        "redirect_uri": dingtalk_allowlist_callback_url(request, source),
        "response_type": "code",
        "scope": " ".join(DINGTALK_ALLOWLIST_SCOPES),
        "state": state,
        "prompt": "consent",
    }
    return f"{DINGTALK_AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}"


class DingTalkAllowlistStatusView(APIView):
    """Return DingTalk allowlist status for a source."""

    permission_classes = [CanViewDingTalkSource]

    def get(self, request: Request, source_slug: str) -> Response:
        source = get_dingtalk_view_source(self, source_slug)
        binding, policy, config = get_dingtalk_allowlist_binding(source)
        source_link_guard = config is not None
        if not source_link_guard:
            binding, policy, config = get_dingtalk_allowlist_binding(source, enabled_only=False)
        return Response(
            {
                "config": config or {"companies": []},
                "managed_policy": {
                    "exists": policy is not None,
                    "pk": str(policy.pk) if policy else None,
                    "name": policy.name if policy else None,
                },
                "policy_binding": {
                    "exists": binding is not None,
                    "pk": str(binding.pk) if binding else None,
                    "enabled": bool(binding.enabled) if binding else False,
                },
                "source_link_guard": {
                    "enabled": source_link_guard,
                },
                "sourceLinkGuard": source_link_guard,
                "callback_url": dingtalk_allowlist_callback_url(request, source),
            }
        )


class DingTalkAllowlistDiscoverStartView(APIView):
    """Start DingTalk allowlist discovery."""

    permission_classes = [CanViewDingTalkSource]

    def post(self, request: Request, source_slug: str) -> Response:
        source = get_dingtalk_view_source(self, source_slug)
        state = create_dingtalk_discovery_state(request, source)
        authorization_url = build_dingtalk_discovery_authorization_url(request, source, state)
        return Response(
            {
                "state": state,
                "authorization_url": authorization_url,
                "url": authorization_url,
            }
        )


class DingTalkAllowlistDepartmentsView(APIView):
    """Fetch DingTalk departments with server-side source credentials."""

    permission_classes = [CanViewDingTalkSource]

    def post(self, request: Request, source_slug: str) -> Response:
        source = get_dingtalk_view_source(self, source_slug)
        corp_id = request.data.get("corp_id") or request.data.get("corpId")
        if not corp_id:
            raise ValidationError({"corp_id": "This field is required."})
        try:
            return Response(fetch_dingtalk_departments(source, str(corp_id)))
        except (RequestException, ValueError) as exc:
            raise ValidationError({"detail": DINGTALK_ALLOWLIST_EXTERNAL_ERROR}) from exc


class DingTalkAllowlistCallbackView(View):
    """OAuth callback for allowlist discovery; never authenticates or links."""

    def get(self, request: HttpRequest, source_slug: str) -> HttpResponse:
        source = get_dingtalk_source(source_slug)
        return handle_dingtalk_discovery_callback(request, source)
