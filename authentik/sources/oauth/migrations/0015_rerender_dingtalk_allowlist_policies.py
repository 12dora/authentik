# This migration previously imported and called live application code
# (``authentik.sources.oauth.types.dingtalk.render_dingtalk_allowlist_policy``) inside a
# ``RunPython`` to re-render deployed managed DingTalk allowlist policies. Coupling an immutable
# migration to mutable render logic makes replays non-deterministic across versions and drags
# runtime models into ``migrate``. It is now a no-op: managed allowlist policies are re-rendered
# by the admin panel on the next save, so there is no need to run live rendering at migrate time.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0014_dingtalk_directory"),
        ("authentik_policies_expression", "0004_expressionpolicy_authentik_p_policy__fb6feb_idx"),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
