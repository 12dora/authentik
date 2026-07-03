# Re-render managed DingTalk allowlist policies so deployed policy bodies pick up
# renderer changes (superuser bypass for application bindings).

from django.db import migrations


def rerender_dingtalk_allowlist_policies(apps, schema_editor):
    from authentik.sources.oauth.types.dingtalk import (
        DINGTALK_ALLOWLIST_MARKER,
        parse_dingtalk_allowlist_policy,
        render_dingtalk_allowlist_policy,
    )

    ExpressionPolicy = apps.get_model("authentik_policies_expression", "ExpressionPolicy")
    db_alias = schema_editor.connection.alias
    for policy in ExpressionPolicy.objects.using(db_alias).filter(
        expression__contains=DINGTALK_ALLOWLIST_MARKER
    ):
        config = parse_dingtalk_allowlist_policy(policy.expression)
        if not config or not config.get("companies"):
            continue
        policy.expression = render_dingtalk_allowlist_policy(config)
        policy.save(update_fields=["expression"])


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0014_dingtalk_directory"),
        ("authentik_policies_expression", "0004_expressionpolicy_authentik_p_policy__fb6feb_idx"),
    ]

    operations = [
        migrations.RunPython(rerender_dingtalk_allowlist_policies, migrations.RunPython.noop),
    ]
