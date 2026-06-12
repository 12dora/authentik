"""authentik OAuth source urls"""

from django.urls import path

from authentik.sources.oauth.api.dingtalk_allowlist import (
    DingTalkAllowlistCallbackView,
    DingTalkAllowlistDepartmentsView,
    DingTalkAllowlistDiscoverStartView,
    DingTalkAllowlistStatusView,
)
from authentik.sources.oauth.api.dingtalk_directory import (
    DingTalkDirectoryDepartmentsView,
    DingTalkDirectoryStatusView,
    DingTalkDirectorySyncView,
    DingTalkDirectoryUserOrgView,
    DingTalkDirectoryUsersView,
)
from authentik.sources.oauth.api.property_mappings import OAuthSourcePropertyMappingViewSet
from authentik.sources.oauth.api.source import OAuthSourceViewSet
from authentik.sources.oauth.api.source_connection import (
    GroupOAuthSourceConnectionViewSet,
    UserOAuthSourceConnectionViewSet,
)
from authentik.sources.oauth.types.registry import RequestKind
from authentik.sources.oauth.views.dispatcher import DispatcherView

urlpatterns = [
    path(
        "login/<slug:source_slug>/",
        DispatcherView.as_view(kind=RequestKind.REDIRECT),
        name="oauth-client-login",
    ),
    path(
        "callback/<slug:source_slug>/",
        DispatcherView.as_view(kind=RequestKind.CALLBACK),
        name="oauth-client-callback",
    ),
    path(
        "callback/<slug:source_slug>/dingtalk-allowlist/",
        DingTalkAllowlistCallbackView.as_view(),
        name="dingtalk-allowlist-callback",
    ),
]

api_urlpatterns = [
    ("propertymappings/source/oauth", OAuthSourcePropertyMappingViewSet),
    ("sources/user_connections/oauth", UserOAuthSourceConnectionViewSet),
    ("sources/group_connections/oauth", GroupOAuthSourceConnectionViewSet),
    ("sources/oauth", OAuthSourceViewSet),
    path(
        "sources/oauth/dingtalk-allowlist/<slug:source_slug>/status/",
        DingTalkAllowlistStatusView.as_view(),
        name="dingtalk-allowlist-status",
    ),
    path(
        "sources/oauth/dingtalk-allowlist/<slug:source_slug>/discover/start/",
        DingTalkAllowlistDiscoverStartView.as_view(),
        name="dingtalk-allowlist-discover-start",
    ),
    path(
        "sources/oauth/dingtalk-allowlist/<slug:source_slug>/departments/",
        DingTalkAllowlistDepartmentsView.as_view(),
        name="dingtalk-allowlist-departments",
    ),
    path(
        "sources/oauth/dingtalk-directory/<slug:source_slug>/status/",
        DingTalkDirectoryStatusView.as_view(),
        name="dingtalk-directory-status",
    ),
    path(
        "sources/oauth/dingtalk-directory/<slug:source_slug>/sync/",
        DingTalkDirectorySyncView.as_view(),
        name="dingtalk-directory-sync",
    ),
    path(
        "sources/oauth/dingtalk-directory/<slug:source_slug>/departments/",
        DingTalkDirectoryDepartmentsView.as_view(),
        name="dingtalk-directory-departments",
    ),
    path(
        "sources/oauth/dingtalk-directory/<slug:source_slug>/users/",
        DingTalkDirectoryUsersView.as_view(),
        name="dingtalk-directory-users",
    ),
    path(
        "sources/oauth/dingtalk-directory/<slug:source_slug>/users/<str:corp_id>/<str:user_id>/org/",
        DingTalkDirectoryUserOrgView.as_view(),
        name="dingtalk-directory-user-org",
    ),
]
