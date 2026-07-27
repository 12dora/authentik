import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0020_reconcile_dingtalk_allowlist_policies"),
    ]

    operations = [
        migrations.CreateModel(
            name="DingTalkDirectoryDepartmentStage",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("corp_id", models.TextField()),
                ("run_id", models.UUIDField()),
                ("dept_id", models.TextField()),
                ("name", models.TextField(blank=True, default="")),
                ("parent_dept_id", models.TextField(blank=True, default="")),
                ("raw", models.JSONField(blank=True, default=dict)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="authentik_sources_oauth.oauthsource",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["source", "corp_id", "run_id"],
                        name="authentik_s_source__567bac_idx",
                    ),
                ],
                "unique_together": {("source", "corp_id", "run_id", "dept_id")},
            },
        ),
        migrations.CreateModel(
            name="DingTalkDirectoryUserStage",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("corp_id", models.TextField()),
                ("run_id", models.UUIDField()),
                ("user_id", models.TextField()),
                ("union_id", models.TextField(blank=True, default="")),
                ("open_id", models.TextField(blank=True, default="")),
                ("name", models.TextField(blank=True, default="")),
                ("avatar", models.TextField(blank=True, default="")),
                ("title", models.TextField(blank=True, default="")),
                ("email", models.TextField(blank=True, default="")),
                ("mobile", models.TextField(blank=True, default="")),
                ("job_number", models.TextField(blank=True, default="")),
                ("manager_user_id", models.TextField(blank=True, default="")),
                ("dept_id_list", models.JSONField(blank=True, default=list)),
                ("active", models.BooleanField(default=True)),
                ("raw", models.JSONField(blank=True, default=dict)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="authentik_sources_oauth.oauthsource",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["source", "corp_id", "run_id"],
                        name="authentik_s_source__98729b_idx",
                    ),
                ],
                "unique_together": {("source", "corp_id", "run_id", "user_id")},
            },
        ),
    ]
