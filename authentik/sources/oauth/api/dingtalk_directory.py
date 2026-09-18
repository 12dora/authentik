"""DingTalk directory cache API."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.utils.translation import gettext_lazy
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED
from rest_framework.views import APIView

from authentik.api.pagination import Pagination
from authentik.core.api.utils import ModelSerializer
from authentik.core.models import USER_ATTRIBUTE_SOURCES, User, UserTypes
from authentik.events.models import Event, EventAction
from authentik.sources.oauth.dingtalk.selectors import (
    get_dingtalk_org_context,
    source_scoped_dingtalk_identity,
)
from authentik.sources.oauth.dingtalk.sync import (
    DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE,
    DINGTALK_SYNC_ERROR_CODES,
    DINGTALK_SYNC_ERROR_UNKNOWN,
    finalize_dingtalk_directory_sync_error,
    queue_dingtalk_directory_sync,
)
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectorySyncStatusChoices,
    DingTalkDirectoryUser,
    OAuthSource,
    UserOAuthSourceConnection,
)
from authentik.sources.oauth.tasks import dingtalk_directory_sync


class DingTalkDirectoryConflict(APIException):
    status_code = 409
    default_code = "dingtalk_directory_conflict"
    default_detail = gettext_lazy("DingTalk directory operation cannot be started for this source.")


def get_dingtalk_source(source_slug: str, *, enabled_only: bool = False) -> OAuthSource:
    queryset = OAuthSource.objects.filter(provider_type="dingtalk")
    if enabled_only:
        queryset = queryset.filter(enabled=True)
    return get_object_or_404(queryset, slug=source_slug)


class CanViewDingTalkDirectory(BasePermission):
    """Require source read access for DingTalk directory endpoints."""

    def has_permission(self, request: Request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        try:
            source = get_dingtalk_source(view.kwargs["source_slug"])
        except Http404:
            # Do not reveal whether a DingTalk source slug exists to callers who lack
            # access; return 403 uniformly for both missing and existing-but-forbidden slugs.
            return False
        view.dingtalk_source = source
        return bool(
            request.user.has_perm("authentik_sources_oauth.view_oauthsource")
            or request.user.has_perm("authentik_sources_oauth.view_oauthsource", source)
        )


def can_change_dingtalk_directory(request: Request, source: OAuthSource) -> bool:
    """Return whether the request user can change the source-scoped DingTalk directory."""
    return bool(
        request.user.has_perm("authentik_sources_oauth.change_oauthsource")
        or request.user.has_perm("authentik_sources_oauth.change_oauthsource", source)
    )


class CanChangeDingTalkDirectory(CanViewDingTalkDirectory):
    """Require source change access for sync trigger endpoints."""

    def has_permission(self, request: Request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        return can_change_dingtalk_directory(request, view.dingtalk_source)


class CanViewDingTalkDirectoryDepartment(CanViewDingTalkDirectory):
    """Require explicit department directory access in addition to source access."""

    def has_permission(self, request: Request, view) -> bool:
        return super().has_permission(request, view) and request.user.has_perm(
            "authentik_sources_oauth.view_dingtalkdirectorydepartment"
        )


class CanViewDingTalkDirectoryUser(CanViewDingTalkDirectory):
    """Require explicit user directory access in addition to source access."""

    def has_permission(self, request: Request, view) -> bool:
        return super().has_permission(request, view) and request.user.has_perm(
            "authentik_sources_oauth.view_dingtalkdirectoryuser"
        )


class CanMaterializeDingTalkDirectoryUser(BasePermission):
    """Require source change access and global user-create permission."""

    def has_permission(self, request: Request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        if not request.user.has_perm("authentik_core.add_user"):
            return False
        try:
            source = get_dingtalk_source(view.kwargs["source_slug"])
        except Http404:
            # Authorized callers get a 404 from the view; everyone else stays at 403
            # so missing slugs are not distinguishable from forbidden ones.
            return bool(request.user.has_perm("authentik_sources_oauth.change_oauthsource"))
        view.dingtalk_source = source
        return can_change_dingtalk_directory(request, source)


class DingTalkDirectoryMaterializeError(APIException):
    """Coded 404/409 for DingTalk directory user materialization."""

    def __init__(self, status_code: int, code: str, detail: str):
        self.status_code = status_code
        super().__init__(detail={"code": code, "detail": detail}, code=code)


class DingTalkDirectorySyncStatusSerializer(ModelSerializer):
    status = serializers.ChoiceField(choices=DingTalkDirectorySyncStatusChoices.choices)
    error = serializers.SerializerMethodField()
    error_code = serializers.SerializerMethodField()
    error_params = serializers.SerializerMethodField()
    counters = serializers.DictField()

    class Meta:
        model = DingTalkDirectorySyncStatus
        fields = [
            "corp_id",
            "status",
            "generation",
            "started_at",
            "finished_at",
            "last_attempt_at",
            "last_success_at",
            "last_full_success_at",
            "error",
            "error_code",
            "error_params",
            "error_correlation_id",
            "counters",
        ]

    def get_error(self, obj: DingTalkDirectorySyncStatus) -> str:
        return self.get_error_code(obj)

    def get_error_code(self, obj: DingTalkDirectorySyncStatus) -> str:
        if obj.error_code in DINGTALK_SYNC_ERROR_CODES:
            return obj.error_code
        if obj.status == DingTalkDirectorySyncStatusChoices.ERROR and obj.error:
            if obj.error in DINGTALK_SYNC_ERROR_CODES:
                return obj.error
            return DINGTALK_SYNC_ERROR_UNKNOWN
        return ""

    def get_error_params(self, obj: DingTalkDirectorySyncStatus) -> dict[str, str]:
        if obj.error_code in DINGTALK_SYNC_ERROR_CODES:
            return obj.error_params if isinstance(obj.error_params, dict) else {}
        if obj.status == DingTalkDirectorySyncStatusChoices.ERROR and obj.error:
            return {"legacy_error": "redacted"}
        return {}


class DingTalkDirectoryStatusSerializer(serializers.Serializer):
    source_slug = serializers.CharField()
    can_change = serializers.BooleanField()
    sync = DingTalkDirectorySyncStatusSerializer(many=True)


class DingTalkDirectorySyncRequestSerializer(serializers.Serializer):
    corp_id = serializers.CharField()
    full = serializers.BooleanField(required=False, default=True)


class DingTalkDirectorySyncQueuedSerializer(serializers.Serializer):
    queued = serializers.BooleanField()
    corp_id = serializers.CharField()
    run_id = serializers.CharField(allow_null=True)


class DingTalkDirectorySyncDeletedSerializer(serializers.Serializer):
    deleted = serializers.BooleanField()
    corp_id = serializers.CharField()


class DingTalkDirectoryOrgContextSerializer(serializers.Serializer):
    corp_id = serializers.CharField(allow_null=True)
    user_id = serializers.CharField(allow_null=True)
    source_slug = serializers.CharField()
    departments = serializers.JSONField()
    manager = serializers.JSONField(allow_null=True)
    manager_chain = serializers.JSONField()
    stale = serializers.BooleanField()
    last_synced_at = serializers.CharField(allow_null=True)


class DingTalkDirectoryDepartmentSerializer(ModelSerializer):
    class Meta:
        model = DingTalkDirectoryDepartment
        fields = [
            "corp_id",
            "dept_id",
            "name",
            "parent_dept_id",
            "is_deleted",
            "last_seen_at",
        ]


class DingTalkDirectoryUserSerializer(ModelSerializer):
    # authentik's ModelSerializer maps JSONField to JSONDictField; this field is a list.
    dept_id_list = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = DingTalkDirectoryUser
        fields = [
            "corp_id",
            "user_id",
            "name",
            "title",
            "avatar",
            "email",
            "mobile",
            "job_number",
            "dept_id_list",
            "manager_user_id",
            "active",
            "is_deleted",
            "last_seen_at",
        ]


class DingTalkDirectoryStatusView(APIView):
    permission_classes = [CanViewDingTalkDirectory]

    @extend_schema(responses={200: DingTalkDirectoryStatusSerializer})
    def get(self, request: Request, source_slug: str) -> Response:
        source = self.dingtalk_source
        statuses = DingTalkDirectorySyncStatus.objects.filter(source=source).order_by("corp_id")
        return Response(
            {
                "source_slug": source.slug,
                "can_change": can_change_dingtalk_directory(request, source),
                "sync": DingTalkDirectorySyncStatusSerializer(statuses, many=True).data,
            }
        )


class DingTalkDirectorySyncView(APIView):
    permission_classes = [CanChangeDingTalkDirectory]

    @extend_schema(
        request=DingTalkDirectorySyncRequestSerializer,
        responses={200: DingTalkDirectorySyncQueuedSerializer},
    )
    def post(self, request: Request, source_slug: str) -> Response:
        source = self.dingtalk_source
        if not source.enabled:
            raise DingTalkDirectoryConflict(gettext_lazy("DingTalk source is disabled."))
        payload = request.data
        if not payload.get("corp_id") and payload.get("corpId"):
            if hasattr(payload, "copy"):
                payload = payload.copy()
                payload["corp_id"] = payload.get("corpId")
            else:
                payload = {**payload, "corp_id": payload.get("corpId")}
        serializer = DingTalkDirectorySyncRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        corp_id = serializer.validated_data["corp_id"]
        full = serializer.validated_data["full"]
        run_id, should_enqueue = queue_dingtalk_directory_sync(source, str(corp_id))
        if should_enqueue:
            try:
                dingtalk_directory_sync.send(str(source.pk), str(corp_id), str(run_id), full=full)
            except RuntimeError as exc:
                finalize_dingtalk_directory_sync_error(
                    source=source,
                    corp_id=str(corp_id),
                    run_id=run_id,
                    exc=exc,
                    error_code=DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE,
                )
                raise DingTalkDirectoryConflict(
                    gettext_lazy("DingTalk directory sync could not be queued.")
                ) from exc
        return Response({"queued": should_enqueue, "corp_id": str(corp_id), "run_id": str(run_id)})

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="corp_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
            )
        ],
        request=None,
        responses={200: DingTalkDirectorySyncDeletedSerializer},
    )
    def delete(self, request: Request, source_slug: str) -> Response:
        source = self.dingtalk_source
        # Prefer the query parameter: request bodies on DELETE are stripped by
        # some proxies. The body keys remain supported for compatibility.
        corp_id = (
            request.query_params.get("corp_id")
            or request.data.get("corp_id")
            or request.data.get("corpId")
        )
        if not corp_id:
            raise ValidationError({"corp_id": gettext_lazy("This field is required.")})
        corp_id = str(corp_id)
        with transaction.atomic():
            (
                status,
                _created,
            ) = DingTalkDirectorySyncStatus.objects.select_for_update().get_or_create(
                source=source, corp_id=corp_id
            )
            status.run_sequence += 1
            status.active_run_id = None
            status.status = DingTalkDirectorySyncStatusChoices.DELETED
            status.started_at = None
            status.finished_at = now()
            status.last_attempt_at = status.finished_at
            status.error = ""
            status.error_code = ""
            status.error_params = {}
            status.error_correlation_id = None
            status.counters = {}
            status.save()
            DingTalkDirectoryDepartment.objects.filter(source=source, corp_id=corp_id).delete()
            DingTalkDirectoryUser.objects.filter(source=source, corp_id=corp_id).delete()
        return Response({"deleted": True, "corp_id": corp_id})


class DingTalkDirectoryDepartmentsView(generics.ListAPIView):
    permission_classes = [CanViewDingTalkDirectoryDepartment]
    serializer_class = DingTalkDirectoryDepartmentSerializer
    pagination_class = Pagination
    queryset = DingTalkDirectoryDepartment.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DingTalkDirectoryDepartment.objects.none()
        source = self.dingtalk_source
        return DingTalkDirectoryDepartment.objects.filter(source=source, is_deleted=False).order_by(
            "corp_id", "dept_id"
        )


class DingTalkDirectoryUsersView(generics.ListAPIView):
    permission_classes = [CanViewDingTalkDirectoryUser]
    serializer_class = DingTalkDirectoryUserSerializer
    pagination_class = Pagination
    queryset = DingTalkDirectoryUser.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DingTalkDirectoryUser.objects.none()
        source = self.dingtalk_source
        return DingTalkDirectoryUser.objects.filter(source=source, is_deleted=False).order_by(
            "corp_id", "user_id"
        )


class DingTalkDirectoryMaterializeRequestSerializer(serializers.Serializer):
    """Empty body; materialize is fully identified by the URL."""


class DingTalkDirectoryMaterializedUserSerializer(serializers.Serializer):
    pk = serializers.IntegerField()
    uuid = serializers.UUIDField()
    username = serializers.CharField()
    name = serializers.CharField()
    is_active = serializers.BooleanField()


class DingTalkDirectoryMaterializeResponseSerializer(serializers.Serializer):
    created = serializers.BooleanField()
    user = DingTalkDirectoryMaterializedUserSerializer()


def dingtalk_attributes_from_directory_user(
    source: OAuthSource, directory_user: DingTalkDirectoryUser
) -> dict[str, Any]:
    """Build the login-shaped ``dingtalk`` attribute blob from a directory row.

    Omits fields the directory cache does not store (raw_profile, role_list, state_code).
    """
    return {
        "source_pk": str(source.pk),
        "source_slug": source.slug,
        "union_id": directory_user.union_id,
        "open_id": directory_user.open_id,
        "user_id": directory_user.user_id,
        "corp_id": directory_user.corp_id,
        "nick": directory_user.name,
        "name": directory_user.name,
        "avatar": directory_user.avatar,
        "title": directory_user.title,
        "mobile": directory_user.mobile,
        "dept_id_list": directory_user.dept_id_list,
        "job_number": directory_user.job_number,
    }


def materialize_dingtalk_directory_user(
    *,
    source: OAuthSource,
    corp_id: str,
    user_id: str,
    request: Request,
) -> tuple[User, bool]:
    """Create or return the Authentik user bound to this DingTalk directory row."""
    try:
        with transaction.atomic():
            try:
                directory_user = DingTalkDirectoryUser.objects.select_for_update().get(
                    source=source,
                    corp_id=corp_id,
                    user_id=user_id,
                    is_deleted=False,
                )
            except DingTalkDirectoryUser.DoesNotExist:
                raise DingTalkDirectoryMaterializeError(
                    404,
                    "directory_user_not_found",
                    gettext_lazy("DingTalk directory user was not found."),
                ) from None
            if not directory_user.active:
                raise DingTalkDirectoryMaterializeError(
                    409,
                    "directory_user_inactive",
                    gettext_lazy("DingTalk directory user is not active."),
                )
            if not directory_user.union_id:
                raise DingTalkDirectoryMaterializeError(
                    409,
                    "union_id_missing",
                    gettext_lazy("DingTalk directory user is missing a unionId."),
                )
            connections = list(
                UserOAuthSourceConnection.objects.select_for_update()
                .select_related("user")
                .filter(source=source, identifier=directory_user.union_id)
            )
            if len(connections) == 1:
                return connections[0].user, False
            if len(connections) > 1:
                raise DingTalkDirectoryMaterializeError(
                    409,
                    "binding_conflict",
                    gettext_lazy("Multiple Authentik users are bound to this DingTalk identity."),
                )
            if User.objects.filter(username=directory_user.user_id).exists():
                raise DingTalkDirectoryMaterializeError(
                    409,
                    "username_conflict",
                    gettext_lazy("An Authentik user with this username already exists."),
                )
            dingtalk = dingtalk_attributes_from_directory_user(source, directory_user)
            user = User(
                username=directory_user.user_id,
                name=directory_user.name,
                email=directory_user.email or "",
                type=UserTypes.INTERNAL,
                path=source.get_user_path(),
                is_active=True,
                attributes={
                    "dingtalk": dingtalk,
                    "dingtalk_sources": {str(source.pk): deepcopy(dingtalk)},
                    USER_ATTRIBUTE_SOURCES: [source.name],
                    "dingtalk_materialized": {
                        "at": now().isoformat(),
                        "by": request.user.username,
                    },
                },
            )
            user.set_unusable_password()
            user.save()
            UserOAuthSourceConnection.objects.create(
                user=user,
                source=source,
                identifier=directory_user.union_id,
            )
            Event.new(
                EventAction.USER_WRITE,
                created=True,
                username=user.username,
                name=user.name,
                source_slug=source.slug,
                corp_id=directory_user.corp_id,
                user_id=directory_user.user_id,
            ).from_http(request)
            return user, True
    except IntegrityError as exc:
        raise DingTalkDirectoryMaterializeError(
            409,
            "username_conflict",
            gettext_lazy("An Authentik user with this username already exists."),
        ) from exc


class DingTalkDirectoryUserMaterializeView(APIView):
    permission_classes = [CanMaterializeDingTalkDirectoryUser]

    @extend_schema(
        request=DingTalkDirectoryMaterializeRequestSerializer,
        responses={
            200: DingTalkDirectoryMaterializeResponseSerializer,
            201: OpenApiResponse(response=DingTalkDirectoryMaterializeResponseSerializer),
        },
    )
    def post(self, request: Request, source_slug: str, corp_id: str, user_id: str) -> Response:
        source = getattr(self, "dingtalk_source", None) or get_dingtalk_source(source_slug)
        serializer = DingTalkDirectoryMaterializeRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        user, created = materialize_dingtalk_directory_user(
            source=source,
            corp_id=str(corp_id),
            user_id=str(user_id),
            request=request,
        )
        payload = DingTalkDirectoryMaterializeResponseSerializer(
            {
                "created": created,
                "user": {
                    "pk": user.pk,
                    "uuid": user.uuid,
                    "username": user.username,
                    "name": user.name,
                    "is_active": user.is_active,
                },
            }
        ).data
        return Response(payload, status=HTTP_201_CREATED if created else 200)


class DingTalkDirectoryUserOrgView(APIView):
    permission_classes = [CanViewDingTalkDirectory]

    @extend_schema(responses={200: DingTalkDirectoryOrgContextSerializer})
    def get(self, request: Request, source_slug: str, corp_id: str, user_id: str) -> Response:
        source = self.dingtalk_source
        own_identity = source_scoped_dingtalk_identity(request.user, source)
        is_own_context = own_identity == (str(corp_id), str(user_id))
        can_view_users = request.user.has_perm("authentik_sources_oauth.view_dingtalkdirectoryuser")
        if not is_own_context and not can_view_users:
            raise PermissionDenied(
                gettext_lazy("Reading other DingTalk users requires directory user access.")
            )
        context_user = (
            request.user
            if is_own_context
            else SimpleNamespace(attributes={"dingtalk": {"corp_id": corp_id, "user_id": user_id}})
        )
        return Response(get_dingtalk_org_context(context_user, source_slug=source_slug))
