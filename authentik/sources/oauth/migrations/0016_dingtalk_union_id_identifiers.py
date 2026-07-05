# Remap existing DingTalk UserOAuthSourceConnection identifiers to the unionId-based identity.
#
# DingTalk logins now use the stable, base-profile unionId as the connection identifier (B1),
# instead of the enhancement-dependent ``corpId:userid``. Without this migration, existing
# connections created under the old scheme would no longer match on the next login and users
# would be re-enrolled as duplicates. Each existing DingTalk connection's identifier is rewritten
# to the unionId already stored on the linked user's ``attributes.dingtalk`` (populated from the
# base profile), skipping any that would collide with an existing connection.

from django.db import migrations


def migrate_dingtalk_identifiers_to_union_id(apps, schema_editor):
    OAuthSource = apps.get_model("authentik_sources_oauth", "OAuthSource")
    UserOAuthSourceConnection = apps.get_model(
        "authentik_sources_oauth", "UserOAuthSourceConnection"
    )
    db_alias = schema_editor.connection.alias

    source_ids = list(
        OAuthSource.objects.using(db_alias)
        .filter(provider_type="dingtalk")
        .values_list("pk", flat=True)
    )
    if not source_ids:
        return

    connections = UserOAuthSourceConnection.objects.using(db_alias).filter(
        source_id__in=source_ids
    )
    # Traverse the FK in SQL (``user__attributes``) to avoid multi-table-inheritance quirks when
    # touching the linked user through the historical model.
    for row in connections.values("pk", "identifier", "source_id", "user__attributes"):
        attributes = row["user__attributes"] or {}
        dingtalk = attributes.get("dingtalk") or {}
        union_id = dingtalk.get("union_id") or dingtalk.get("unionId")
        if not union_id or row["identifier"] == union_id:
            continue
        collision = (
            UserOAuthSourceConnection.objects.using(db_alias)
            .filter(source_id=row["source_id"], identifier=union_id)
            .exclude(pk=row["pk"])
            .exists()
        )
        if collision:
            continue
        UserOAuthSourceConnection.objects.using(db_alias).filter(pk=row["pk"]).update(
            identifier=union_id
        )


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0015_rerender_dingtalk_allowlist_policies"),
    ]

    operations = [
        migrations.RunPython(
            migrate_dingtalk_identifiers_to_union_id, migrations.RunPython.noop
        ),
    ]
