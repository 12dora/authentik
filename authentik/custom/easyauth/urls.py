"""EasyAuth custom API urls."""

from django.urls import path

from authentik.custom.easyauth.api.dingtalk_managed_users import (
    DingTalkManagedUsersByManagerView,
)

api_urlpatterns = [
    path(
        "sources/oauth/dingtalk-directory/"
        "<slug:source_slug>/managed-users/by-manager/<str:corp_id>/<str:manager_user_id>/",
        DingTalkManagedUsersByManagerView.as_view(),
        name="easyauth-dingtalk-managed-users-by-manager",
    ),
]
