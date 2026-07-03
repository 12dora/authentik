"""EasyAuth-facing DingTalk managed-users API."""

from typing import Any

from django.shortcuts import get_object_or_404
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from rest_framework.views import APIView

from authentik.sources.oauth.api.dingtalk_directory import CanViewDingTalkDirectoryUser
from authentik.sources.oauth.dingtalk.managed_users import (
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
        try:
            return Response(
                _without_sensitive_fields(
                    get_dingtalk_managed_users(source, corp_id, manager_user_id)
                )
            )
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
