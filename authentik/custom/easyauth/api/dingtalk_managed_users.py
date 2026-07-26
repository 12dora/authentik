"""EasyAuth-facing DingTalk managed-users API."""

from typing import Any

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from rest_framework.views import APIView

from authentik.sources.oauth.api.dingtalk_directory import CanViewDingTalkDirectoryUser
from authentik.sources.oauth.dingtalk.managed_users import (
    DEFAULT_MANAGED_USERS_PAGE_SIZE,
    MAX_MANAGED_USERS_PAGE_SIZE,
    MAX_MANAGED_USERS_RESULTS,
    DingTalkBindingConflict,
    DingTalkManagerNotFound,
    DingTalkSourceUnavailable,
    get_dingtalk_managed_users,
)
from authentik.sources.oauth.models import OAuthSource

SENSITIVE_RESPONSE_KEYS = {
    "consumer_secret",
    "email",
    "mobile",
    "open_id",
    "raw",
    "union_id",
}


class DingTalkManagedUsersQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, required=False, default=1)
    page_size = serializers.IntegerField(
        min_value=1,
        max_value=MAX_MANAGED_USERS_PAGE_SIZE,
        required=False,
        default=DEFAULT_MANAGED_USERS_PAGE_SIZE,
    )
    max_results = serializers.IntegerField(
        min_value=1,
        max_value=MAX_MANAGED_USERS_RESULTS,
        required=False,
        default=MAX_MANAGED_USERS_RESULTS,
    )


class DingTalkManagedUserSerializer(serializers.Serializer):
    source_user_id = serializers.CharField()
    source_identifier = serializers.CharField()
    directory_active = serializers.BooleanField()
    is_deleted = serializers.BooleanField()
    authentik_subject = serializers.CharField(allow_null=True)
    authentik_subject_type = serializers.CharField(allow_null=True)
    authentik_user_active = serializers.BooleanField(allow_null=True)
    binding_status = serializers.ChoiceField(choices=["bound", "unbound"])
    diagnostics = serializers.JSONField()


class DingTalkManagedUsersPaginationSerializer(serializers.Serializer):
    next = serializers.IntegerField()
    previous = serializers.IntegerField()
    count = serializers.IntegerField()
    current = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    page_size = serializers.IntegerField()


class DingTalkManagedUsersResponseSerializer(serializers.Serializer):
    source_slug = serializers.CharField()
    corp_id = serializers.CharField()
    manager_user_id = serializers.CharField()
    resolver = serializers.CharField()
    resolved_at = serializers.CharField()
    stale = serializers.BooleanField()
    last_synced_at = serializers.CharField(allow_null=True)
    diagnostics = serializers.JSONField()
    pagination = DingTalkManagedUsersPaginationSerializer()
    users = DingTalkManagedUserSerializer(many=True)


class DingTalkManagedUsersErrorSerializer(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=["source_unavailable", "manager_not_found", "binding_conflict"]
    )
    detail = serializers.CharField()


def _without_sensitive_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_sensitive_fields(item)
            for key, item in value.items()
            if key not in SENSITIVE_RESPONSE_KEYS
        }
    if isinstance(value, list):
        return [_without_sensitive_fields(item) for item in value]
    return value


class DingTalkManagedUsersByManagerView(APIView):
    """Expose DingTalk managed users for downstream EasyAuth resolution."""

    permission_classes = [CanViewDingTalkDirectoryUser]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="page_size", type=int, required=False),
            OpenApiParameter(name="max_results", type=int, required=False),
        ],
        responses={
            200: DingTalkManagedUsersResponseSerializer,
            404: DingTalkManagedUsersErrorSerializer,
            409: DingTalkManagedUsersErrorSerializer,
        },
    )
    def get(
        self,
        request: Request,
        source_slug: str,
        corp_id: str,
        manager_user_id: str,
    ) -> Response:
        source = get_object_or_404(
            OAuthSource,
            slug=source_slug,
            provider_type="dingtalk",
            enabled=True,
        )
        query = DingTalkManagedUsersQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            payload = _without_sensitive_fields(
                get_dingtalk_managed_users(
                    source,
                    corp_id,
                    manager_user_id,
                    page=query.validated_data["page"],
                    page_size=query.validated_data["page_size"],
                    max_results=query.validated_data["max_results"],
                )
            )
            response = DingTalkManagedUsersResponseSerializer(data=payload)
            response.is_valid(raise_exception=True)
            return Response(response.data)
        except DingTalkSourceUnavailable as exc:
            return Response(
                {"code": "source_unavailable", "detail": str(exc)},
                status=HTTP_404_NOT_FOUND,
            )
        except DingTalkManagerNotFound as exc:
            return Response(
                {"code": "manager_not_found", "detail": str(exc)},
                status=HTTP_404_NOT_FOUND,
            )
        except DingTalkBindingConflict as exc:
            return Response(
                {"code": "binding_conflict", "detail": str(exc)},
                status=HTTP_409_CONFLICT,
            )
