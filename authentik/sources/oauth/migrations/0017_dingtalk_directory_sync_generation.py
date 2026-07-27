from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("authentik_sources_oauth", "0016_dingtalk_union_id_identifiers")]

    operations = [
        migrations.AddField(
            model_name="dingtalkdirectorysyncstatus",
            name="generation",
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
