# Merge fork DingTalk migrations with upstream 2026.8.0 URL textfield changes.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("authentik_sources_oauth", "0015_oauthsource_url_textfields"),
        ("authentik_sources_oauth", "0023_reconcile_dingtalk_allowlist_denial_messages"),
    ]

    operations = []
