"""DingTalk directory user materialize API tests."""

from django.contrib.auth.models import AnonymousUser
from django.urls import reverse
from django.utils.timezone import now
from rest_framework.test import APITestCase

from authentik.core.models import USER_ATTRIBUTE_SOURCES, User, UserTypes
from authentik.core.sources.matcher import Action, SourceMatcher
from authentik.core.tests.utils import RequestFactory, create_test_admin_user, create_test_user
from authentik.events.models import Event, EventAction
from authentik.sources.oauth.models import (
    DingTalkDirectoryUser,
    GroupOAuthSourceConnection,
    OAuthSource,
    UserOAuthSourceConnection,
)
from authentik.sources.oauth.views.callback import OAuthSourceFlowManager


class TestDingTalkDirectoryMaterializeAPI(APITestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )
        self.directory_user = DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="张甜",
            mobile="13800000000",
            email="ada@example.invalid",
            job_number="E-001",
            union_id="UNION",
            open_id="OPEN",
            avatar="https://example.invalid/avatar.png",
            title="Engineer",
            dept_id_list=["1"],
            active=True,
            last_seen_at=now(),
        )
        self.admin = create_test_admin_user("materialize-admin")

    def authenticate(self, user):
        self.client.force_login(user)
        self.client.force_authenticate(user=user)

    def materialize_url(self, *, corp_id="CORP", user_id="USER", source_slug="dingtalk"):
        return reverse(
            "authentik_api:dingtalk-directory-user-materialize",
            kwargs={
                "source_slug": source_slug,
                "corp_id": corp_id,
                "user_id": user_id,
            },
        )

    def post_materialize(self, **kwargs):
        return self.client.post(self.materialize_url(**kwargs), data={}, format="json")

    def test_materialize_creates_user_and_connection(self):
        self.authenticate(self.admin)

        response = self.post_materialize()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["created"])
        user = User.objects.get(username="USER")
        self.assertEqual(body["user"]["pk"], user.pk)
        self.assertEqual(body["user"]["uuid"], str(user.uuid))
        self.assertEqual(body["user"]["username"], "USER")
        self.assertEqual(body["user"]["name"], "张甜")
        self.assertTrue(body["user"]["is_active"])
        self.assertEqual(user.name, "张甜")
        self.assertEqual(user.email, "ada@example.invalid")
        self.assertEqual(user.type, UserTypes.INTERNAL)
        self.assertEqual(user.path, "goauthentik.io/sources/dingtalk")
        self.assertTrue(user.is_active)
        self.assertFalse(user.has_usable_password())
        dingtalk = user.attributes["dingtalk"]
        self.assertEqual(dingtalk["source_pk"], str(self.source.pk))
        self.assertEqual(dingtalk["source_slug"], "dingtalk")
        self.assertEqual(dingtalk["union_id"], "UNION")
        self.assertEqual(dingtalk["open_id"], "OPEN")
        self.assertEqual(dingtalk["user_id"], "USER")
        self.assertEqual(dingtalk["corp_id"], "CORP")
        self.assertEqual(dingtalk["nick"], "张甜")
        self.assertEqual(dingtalk["name"], "张甜")
        self.assertEqual(dingtalk["avatar"], "https://example.invalid/avatar.png")
        self.assertEqual(dingtalk["title"], "Engineer")
        self.assertEqual(dingtalk["mobile"], "13800000000")
        self.assertEqual(dingtalk["dept_id_list"], ["1"])
        self.assertEqual(dingtalk["job_number"], "E-001")
        self.assertNotIn("raw_profile", dingtalk)
        self.assertNotIn("role_list", dingtalk)
        self.assertNotIn("state_code", dingtalk)
        self.assertEqual(user.attributes["dingtalk_sources"][str(self.source.pk)], dingtalk)
        self.assertEqual(user.attributes[USER_ATTRIBUTE_SOURCES], [self.source.name])
        self.assertEqual(user.attributes["dingtalk_materialized"]["by"], "materialize-admin")
        self.assertTrue(user.attributes["dingtalk_materialized"]["at"])
        connection = UserOAuthSourceConnection.objects.get(source=self.source, identifier="UNION")
        self.assertEqual(connection.user, user)
        self.assertIsNone(connection.access_token)
        event = Event.objects.filter(action=EventAction.USER_WRITE, context__created=True).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.context["source_slug"], "dingtalk")
        self.assertEqual(event.context["corp_id"], "CORP")
        self.assertEqual(event.context["user_id"], "USER")
        self.assertNotIn("union_id", event.context)
        self.assertNotIn("mobile", event.context)
        self.assertNotIn("UNION", str(event.context))
        self.assertNotIn("13800000000", str(event.context))

    def test_materialize_is_idempotent(self):
        self.authenticate(self.admin)
        created = self.post_materialize()
        self.assertEqual(created.status_code, 201)
        user_pk = created.json()["user"]["pk"]
        materialized_at = User.objects.get(pk=user_pk).attributes["dingtalk_materialized"]["at"]

        response = self.post_materialize()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "created": False,
                "user": created.json()["user"],
            },
        )
        self.assertEqual(User.objects.filter(username="USER").count(), 1)
        self.assertEqual(
            UserOAuthSourceConnection.objects.filter(
                source=self.source, identifier="UNION"
            ).count(),
            1,
        )
        self.assertEqual(
            User.objects.get(pk=user_pk).attributes["dingtalk_materialized"]["at"],
            materialized_at,
        )

    def test_materialize_inactive_directory_user(self):
        self.directory_user.active = False
        self.directory_user.save(update_fields=["active"])
        self.authenticate(self.admin)

        response = self.post_materialize()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "directory_user_inactive")
        self.assertFalse(User.objects.filter(username="USER").exists())

    def test_materialize_deleted_directory_user_not_found(self):
        self.directory_user.is_deleted = True
        self.directory_user.save(update_fields=["is_deleted"])
        self.authenticate(self.admin)

        response = self.post_materialize()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "directory_user_not_found")

    def test_materialize_unknown_directory_user_not_found(self):
        self.authenticate(self.admin)

        response = self.post_materialize(user_id="MISSING")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "directory_user_not_found")

    def test_materialize_unknown_source_not_found(self):
        self.authenticate(self.admin)

        response = self.post_materialize(source_slug="missing")

        self.assertEqual(response.status_code, 404)

    def test_materialize_union_id_missing(self):
        self.directory_user.union_id = ""
        self.directory_user.save(update_fields=["union_id"])
        self.authenticate(self.admin)

        response = self.post_materialize()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "union_id_missing")
        self.assertFalse(User.objects.filter(username="USER").exists())

    def test_materialize_username_conflict(self):
        existing = User(username="USER", name="existing", email="existing@example.invalid")
        existing.set_unusable_password()
        existing.save()
        self.authenticate(self.admin)

        response = self.post_materialize()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "username_conflict")
        self.assertFalse(
            UserOAuthSourceConnection.objects.filter(
                source=self.source, identifier="UNION"
            ).exists()
        )

    def test_materialize_binding_conflict(self):
        first = User(username="first", name="First")
        first.set_unusable_password()
        first.save()
        second = User(username="second", name="Second")
        second.set_unusable_password()
        second.save()
        UserOAuthSourceConnection.objects.create(user=first, source=self.source, identifier="UNION")
        UserOAuthSourceConnection.objects.create(
            user=second, source=self.source, identifier="UNION"
        )
        self.authenticate(self.admin)

        response = self.post_materialize()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "binding_conflict")
        self.assertFalse(User.objects.filter(username="USER").exists())

    def test_materialize_permission_denied(self):
        self.authenticate(create_test_user("regular"))

        response = self.post_materialize()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username="USER").exists())

    def test_materialize_requires_add_user_and_change_source(self):
        changer = create_test_user("changer")
        changer.assign_perms_to_managed_role("authentik_sources_oauth.change_oauthsource")
        self.authenticate(changer)
        missing_add = self.post_materialize()
        self.assertEqual(missing_add.status_code, 403)

        adder = create_test_user("adder")
        adder.assign_perms_to_managed_role("authentik_core.add_user")
        self.authenticate(adder)
        missing_change = self.post_materialize()
        self.assertEqual(missing_change.status_code, 403)

        both = create_test_user("both")
        both.assign_perms_to_managed_role("authentik_sources_oauth.change_oauthsource", self.source)
        both.assign_perms_to_managed_role("authentik_core.add_user")
        self.authenticate(both)
        allowed = self.post_materialize()
        self.assertEqual(allowed.status_code, 201)
        self.assertTrue(User.objects.filter(username="USER").exists())

    def test_materialize_then_source_matcher_authenticates_existing_user(self):
        self.authenticate(self.admin)
        created = self.post_materialize()
        self.assertEqual(created.status_code, 201)
        user_count = User.objects.count()
        materialized = User.objects.get(username="USER")

        matcher = SourceMatcher(self.source, UserOAuthSourceConnection, GroupOAuthSourceConnection)
        action, connection = matcher.get_user_action("UNION", {"username": "USER"})
        self.assertEqual(action, Action.AUTH)
        self.assertEqual(connection.user_id, materialized.pk)

        request = RequestFactory().get("/", user=AnonymousUser())
        flow_manager = OAuthSourceFlowManager(
            self.source,
            request,
            "UNION",
            {"info": {"userid": "USER", "unionId": "UNION", "name": "张甜"}},
            {},
        )
        action, connection = flow_manager.get_action()
        self.assertEqual(action, Action.AUTH)
        self.assertEqual(connection.user_id, materialized.pk)
        self.assertEqual(User.objects.count(), user_count)
        self.assertEqual(User.objects.filter(username="USER").count(), 1)
