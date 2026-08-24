"""DingTalk allowlist discovery API."""

from hashlib import sha256
from urllib.parse import quote, urlencode

from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.views import View
from drf_spectacular.utils import extend_schema
from requests.exceptions import JSONDecodeError, RequestException
from rest_framework import serializers
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_409_CONFLICT, HTTP_502_BAD_GATEWAY
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from structlog.stdlib import get_logger

from authentik.policies.expression.models import ExpressionPolicy
from authentik.policies.models import PolicyBinding
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_ALLOWLIST_MARKER,
    DINGTALK_ALLOWLIST_SCOPES,
    DINGTALK_AUTHORIZE_URL,
    DingTalkDepartmentCorpUnavailable,
    DingTalkDepartmentLoadFailed,
    _redact_dingtalk_detail,
    create_dingtalk_discovery_state,
    dingtalk_oauth_callback_url,
    evaluate_dingtalk_allowlist,
    fetch_dingtalk_departments,
    get_dingtalk_allowlist_binding,
    handle_dingtalk_discovery_callback,
    normalize_dingtalk_allowlist_config,
    parse_dingtalk_allowlist_policy,
    render_dingtalk_allowlist_policy,
)

LOGGER = get_logger()


class DingTalkDepartmentDiscoveryThrottle(UserRateThrottle):
    """Rate-limit server-credentialed DingTalk department discovery to curb enumeration."""

    scope = "dingtalk-department-discovery"

    def get_rate(self) -> str:
        return "60/min"


__all__ = [
    "DINGTALK_ALLOWLIST_MARKER",
    "evaluate_dingtalk_allowlist",
    "parse_dingtalk_allowlist_policy",
    "render_dingtalk_allowlist_policy",
]


def dingtalk_department_public_error(
    code: str,
    params: dict | None = None,
) -> dict:
    return {
        "code": code,
        "params": params or {},
    }


class DingTalkDepartmentAccessDenied(APIException):
    status_code = 400
    default_detail = dingtalk_department_public_error("department_access_denied")
    default_code = "department_access_denied"


class DingTalkDepartmentDependencyUnavailable(APIException):
    status_code = 503
    default_detail = dingtalk_department_public_error("department_dependency_unavailable")
    default_code = "department_dependency_unavailable"


class DingTalkDepartmentInvalidResponse(APIException):
    status_code = HTTP_502_BAD_GATEWAY
    default_detail = dingtalk_department_public_error("department_response_invalid")
    default_code = "department_response_invalid"


class DingTalkAllowlistRevisionConflict(APIException):
    status_code = HTTP_409_CONFLICT
    default_detail = {
        "code": "revision_conflict",
        "detail": _("DingTalk allowlist configuration changed. Refresh and retry."),
    }
    default_code = "revision_conflict"


class DingTalkAllowlistManagedPolicySerializer(serializers.Serializer):
    exists = serializers.BooleanField()
    pk = serializers.CharField(allow_null=True)
    name = serializers.CharField(allow_null=True)


class DingTalkAllowlistPolicyBindingSerializer(serializers.Serializer):
    exists = serializers.BooleanField()
    pk = serializers.CharField(allow_null=True)
    enabled = serializers.BooleanField()
    target = serializers.CharField(required=False)


class DingTalkAllowlistSourceLinkGuardSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()


class DingTalkAllowlistStatusResponseSerializer(serializers.Serializer):
    revision = serializers.CharField()
    can_manage = serializers.BooleanField()
    config = serializers.JSONField()
    managed_policy = DingTalkAllowlistManagedPolicySerializer()
    policy_binding = DingTalkAllowlistPolicyBindingSerializer()
    policy_bindings = DingTalkAllowlistPolicyBindingSerializer(many=True)
    source_link_guard = DingTalkAllowlistSourceLinkGuardSerializer()
    callback_url = serializers.CharField()


class DingTalkAllowlistApplyRequestSerializer(serializers.Serializer):
    config = serializers.JSONField()
    expected_revision = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class DingTalkAllowlistRemoveRequestSerializer(serializers.Serializer):
    expected_revision = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class DingTalkAllowlistDiscoverStartResponseSerializer(serializers.Serializer):
    state = serializers.CharField()
    authorization_url = serializers.CharField()
    url = serializers.CharField()


class DingTalkAllowlistDepartmentsRequestSerializer(serializers.Serializer):
    corp_id = serializers.CharField(required=False)


class DingTalkAllowlistDepartmentsResponseSerializer(serializers.Serializer):
    corp_id = serializers.CharField()
    label = serializers.CharField(required=False, allow_blank=True)
    departments = serializers.JSONField()


def get_dingtalk_source(source_slug: str) -> OAuthSource:
    """Get an enabled DingTalk OAuth source by slug."""
    source = get_object_or_404(OAuthSource, slug=source_slug, enabled=True)
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


def can_manage_dingtalk_source(request: Request | HttpRequest, source: OAuthSource) -> bool:
    """Return true when the request user can mutate this DingTalk source."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    return bool(
        user.has_perm("authentik_sources_oauth.change_oauthsource")
        or user.has_perm("authentik_sources_oauth.change_oauthsource", source)
    )


class CanManageDingTalkSource(CanViewDingTalkSource):
    """Require RBAC write access to mutate the managed DingTalk allowlist policy."""

    def has_permission(self, request: Request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        return can_manage_dingtalk_source(request, view.dingtalk_source)


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


def _managed_policy_name(source: OAuthSource) -> str:
    return f"dingtalk-allowlist-{source.slug}"


def _allowlist_targets(source: OAuthSource):
    targets = [source]
    for flow in (source.authentication_flow, source.enrollment_flow):
        if flow and flow not in targets:
            targets.append(flow)
    return targets


def _policy_bindings(policy: ExpressionPolicy | None) -> list[PolicyBinding]:
    if not policy:
        return []
    return list(PolicyBinding.objects.filter(policy=policy).order_by("target_id", "order", "pk"))


def _revision(policy: ExpressionPolicy | None) -> str:
    if not policy:
        return "none"
    parts = [
        str(policy.pk),
        policy.expression,
        *[
            f"{binding.pk}:{binding.target_id}:{binding.enabled}:{binding.order}"
            for binding in _policy_bindings(policy)
        ],
    ]
    return sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _binding_payload(binding: PolicyBinding | None) -> dict:
    return {
        "exists": binding is not None,
        "pk": str(binding.pk) if binding else None,
        "enabled": bool(binding.enabled) if binding else False,
        "target": str(binding.target_id) if binding else "",
    }


def dingtalk_allowlist_status_payload(
    request: HttpRequest | Request,
    source: OAuthSource,
) -> dict:
    binding, policy, config = get_dingtalk_allowlist_binding(source)
    source_link_guard = config is not None
    if not source_link_guard:
        binding, policy, config = get_dingtalk_allowlist_binding(source, enabled_only=False)
    bindings = _policy_bindings(policy)
    return {
        "revision": _revision(policy),
        "can_manage": can_manage_dingtalk_source(request, source),
        "config": config or {"companies": []},
        "managed_policy": {
            "exists": policy is not None,
            "pk": str(policy.pk) if policy else None,
            "name": policy.name if policy else None,
        },
        "policy_binding": _binding_payload(binding),
        "policy_bindings": [_binding_payload(item) for item in bindings],
        "source_link_guard": {
            "enabled": source_link_guard,
        },
        "sourceLinkGuard": source_link_guard,
        "callback_url": dingtalk_allowlist_callback_url(request, source),
    }


def _assert_revision(
    expected_revision: str | None,
    current_revision: str,
    *,
    idempotent: bool = False,
) -> None:
    if expected_revision in (None, "") or expected_revision == current_revision or idempotent:
        return
    raise DingTalkAllowlistRevisionConflict()


def _managed_policy_for_update(
    source: OAuthSource,
) -> tuple[PolicyBinding | None, ExpressionPolicy | None, dict | None]:
    binding, policy, config = get_dingtalk_allowlist_binding(source, enabled_only=False)
    if policy:
        return binding, policy, config
    policy = ExpressionPolicy.objects.filter(name=_managed_policy_name(source)).first()
    if not policy:
        return None, None, None
    if DINGTALK_ALLOWLIST_MARKER not in policy.expression:
        raise ValidationError(
            {
                "detail": _(
                    "A policy with the managed DingTalk allowlist name already exists "
                    "but is not managed."
                )
            }
        )
    return None, policy, parse_dingtalk_allowlist_policy(policy.expression)


def apply_dingtalk_allowlist_configuration(
    source: OAuthSource,
    config: dict,
    expected_revision: str | None,
) -> None:
    normalized = normalize_dingtalk_allowlist_config(config)
    with transaction.atomic():
        _, policy, current_config = _managed_policy_for_update(source)
        _assert_revision(
            expected_revision,
            _revision(policy),
            idempotent=current_config == normalized,
        )
        expression = render_dingtalk_allowlist_policy(
            normalized,
            source_slug=source.slug,
            source_pk=str(source.pk),
        )
        if policy:
            policy.expression = expression
            policy.execution_logging = False
            policy.save(update_fields=["expression", "execution_logging"])
        else:
            policy = ExpressionPolicy.objects.create(
                name=_managed_policy_name(source),
                expression=expression,
                execution_logging=False,
            )
        for target in _allowlist_targets(source):
            existing = list(
                PolicyBinding.objects.filter(target_id=target.pbm_uuid, policy=policy).order_by(
                    "order", "pk"
                )
            )
            if existing:
                binding = existing[0]
                if not binding.enabled:
                    binding.enabled = True
                    binding.save(update_fields=["enabled"])
                continue
            max_order = (
                PolicyBinding.objects.filter(target_id=target.pbm_uuid)
                .order_by("-order")
                .values_list("order", flat=True)
                .first()
                or 0
            )
            PolicyBinding.objects.create(
                target=target,
                policy=policy,
                enabled=True,
                order=max_order + 10,
                timeout=30,
                failure_result=False,
            )


def remove_dingtalk_allowlist_configuration(
    source: OAuthSource,
    expected_revision: str | None,
) -> None:
    with transaction.atomic():
        _, policy, _ = _managed_policy_for_update(source)
        _assert_revision(expected_revision, _revision(policy), idempotent=policy is None)
        if not policy:
            return
        for binding in _policy_bindings(policy):
            binding.delete()
        policy.delete()


class DingTalkAllowlistStatusView(APIView):
    """Return DingTalk allowlist status for a source."""

    permission_classes = [CanViewDingTalkSource]

    @extend_schema(responses={200: DingTalkAllowlistStatusResponseSerializer})
    def get(self, request: Request, source_slug: str) -> Response:
        source = get_dingtalk_view_source(self, source_slug)
        return Response(dingtalk_allowlist_status_payload(request, source))


class DingTalkAllowlistApplyView(APIView):
    """Transactionally apply the source-scoped managed DingTalk allowlist policy."""

    permission_classes = [CanManageDingTalkSource]

    @extend_schema(
        request=DingTalkAllowlistApplyRequestSerializer,
        responses={200: DingTalkAllowlistStatusResponseSerializer, 409: None},
    )
    def post(self, request: Request, source_slug: str) -> Response:
        source = get_dingtalk_view_source(self, source_slug)
        serializer = DingTalkAllowlistApplyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        apply_dingtalk_allowlist_configuration(
            source,
            serializer.validated_data["config"],
            serializer.validated_data.get("expected_revision"),
        )
        return Response(dingtalk_allowlist_status_payload(request, source))


class DingTalkAllowlistRemoveView(APIView):
    """Transactionally remove the source-scoped managed DingTalk allowlist policy."""

    permission_classes = [CanManageDingTalkSource]

    @extend_schema(
        request=DingTalkAllowlistRemoveRequestSerializer,
        responses={200: DingTalkAllowlistStatusResponseSerializer, 409: None},
    )
    def post(self, request: Request, source_slug: str) -> Response:
        source = get_dingtalk_view_source(self, source_slug)
        serializer = DingTalkAllowlistRemoveRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remove_dingtalk_allowlist_configuration(
            source,
            serializer.validated_data.get("expected_revision"),
        )
        return Response(dingtalk_allowlist_status_payload(request, source))


class DingTalkAllowlistDiscoverStartView(APIView):
    """Start DingTalk allowlist discovery."""

    permission_classes = [CanViewDingTalkSource]

    @extend_schema(
        request=None,
        responses={200: DingTalkAllowlistDiscoverStartResponseSerializer},
    )
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
    # Throttle this server-credentialed endpoint. Note the queryable corp is already bounded
    # by DingTalk itself — fetch_dingtalk_departments raises DingTalkDepartmentCorpUnavailable for
    # any corp this app is not authorized for — so throttling only curbs abusive enumeration.
    throttle_classes = [DingTalkDepartmentDiscoveryThrottle]

    @extend_schema(
        request=DingTalkAllowlistDepartmentsRequestSerializer,
        responses={200: DingTalkAllowlistDepartmentsResponseSerializer},
    )
    def post(self, request: Request, source_slug: str) -> Response:
        source = get_dingtalk_view_source(self, source_slug)
        corp_id = request.data.get("corp_id") or request.data.get("corpId")
        if not corp_id:
            raise ValidationError({"corp_id": _("This field is required.")})
        try:
            return Response(fetch_dingtalk_departments(source, str(corp_id)))
        except DingTalkDepartmentCorpUnavailable as exc:
            LOGGER.warning(
                "dingtalk_department_access_denied",
                source_slug=source.slug,
                corp_id=str(corp_id),
                detail=_redact_dingtalk_detail(exc),
            )
            raise DingTalkDepartmentAccessDenied(
                detail=dingtalk_department_public_error(
                    "department_access_denied",
                    {"corp_id": str(corp_id)},
                )
            ) from exc
        except JSONDecodeError as exc:
            LOGGER.warning(
                "dingtalk_department_response_invalid",
                source_slug=source.slug,
                corp_id=str(corp_id),
                detail=_redact_dingtalk_detail(exc),
            )
            raise DingTalkDepartmentInvalidResponse(
                detail=dingtalk_department_public_error(
                    "department_response_invalid",
                    {"corp_id": str(corp_id)},
                )
            ) from exc
        except (DingTalkDepartmentLoadFailed, RequestException) as exc:
            LOGGER.warning(
                "dingtalk_department_dependency_unavailable",
                source_slug=source.slug,
                corp_id=str(corp_id),
                detail=_redact_dingtalk_detail(exc),
            )
            raise DingTalkDepartmentDependencyUnavailable(
                detail=dingtalk_department_public_error(
                    "department_dependency_unavailable",
                    {"corp_id": str(corp_id)},
                )
            ) from exc
        except ValueError as exc:
            LOGGER.warning(
                "dingtalk_department_response_invalid",
                source_slug=source.slug,
                corp_id=str(corp_id),
                detail=_redact_dingtalk_detail(exc),
            )
            raise DingTalkDepartmentInvalidResponse(
                detail=dingtalk_department_public_error(
                    "department_response_invalid",
                    {"corp_id": str(corp_id)},
                )
            ) from exc


class DingTalkAllowlistCallbackView(View):
    """OAuth callback for allowlist discovery; never authenticates or links."""

    def get(self, request: HttpRequest, source_slug: str) -> HttpResponse:
        source = get_dingtalk_source(source_slug)
        return handle_dingtalk_discovery_callback(request, source)
