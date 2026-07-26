"""DingTalk directory cache API."""

from types import SimpleNamespace

from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.utils.translation import gettext_lazy
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, serializers
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from authentik.api.pagination import Pagination
from authentik.sources.oauth.dingtalk.selectors import (
    get_dingtalk_org_context,
    source_scoped_dingtalk_identity,
)
from authentik.sources.oauth.dingtalk.sync import (
    DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE,
    finalize_dingtalk_directory_sync_error,
    queue_dingtalk_directory_sync,
)
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectorySyncStatusChoices,
    DingTalkDirectoryUser,
    OAuthSource,
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


class DingTalkDirectorySyncStatusSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(choices=DingTalkDirectorySyncStatusChoices.choices)
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
            "error",
            "error_code",
            "error_params",
            "error_correlation_id",
            "counters",
        ]


class DingTalkDirectoryStatusSerializer(serializers.Serializer):
    source_slug = serializers.CharField()
    can_change = serializers.BooleanField()
    sync = DingTalkDirectorySyncStatusSerializer(many=True)


class DingTalkDirectorySyncRequestSerializer(serializers.Serializer):
    corp_id = serializers.CharField()


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


class DingTalkDirectoryDepartmentSerializer(serializers.ModelSerializer):
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


class DingTalkDirectoryUserSerializer(serializers.ModelSerializer):
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
        corp_id = request.data.get("corp_id") or request.data.get("corpId")
        if not corp_id:
            raise ValidationError({"corp_id": gettext_lazy("This field is required.")})
        run_id, should_enqueue = queue_dingtalk_directory_sync(source, str(corp_id))
        if should_enqueue:
            try:
                dingtalk_directory_sync.send(str(source.pk), str(corp_id), str(run_id))
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
            status, _created = (
                DingTalkDirectorySyncStatus.objects.select_for_update().get_or_create(
                    source=source, corp_id=corp_id
                )
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
