"""Drop stale daily DingTalk directory-sync Schedule rows so startup recreate them.

``ScheduleSpec.update_or_create`` puts ``crontab`` only in ``create_values``.
Tenant reconcile therefore leaves existing rows on the old two-hourly crontab.
Deleting the actor's rows is a no-op when the schedules table (or the row) is
absent. django-tenants already runs this migration once per schema, so this
must not iterate tenants or switch schema context.
"""

from django.db import migrations
from django.db.utils import OperationalError, ProgrammingError

DINGTALK_DIRECTORY_SYNC_ALL_ACTOR = "authentik.sources.oauth.tasks.dingtalk_directory_sync_all"


def delete_stale_dingtalk_directory_sync_all_schedules(apps, schema_editor):
    try:
        Schedule = apps.get_model("authentik_tasks_schedules", "Schedule")
    except LookupError:
        return
    connection = schema_editor.connection
    try:
        table_names = set(connection.introspection.table_names())
    except OperationalError, ProgrammingError:
        return
    if Schedule._meta.db_table not in table_names:
        return
    try:
        Schedule.objects.using(connection.alias).filter(
            actor_name=DINGTALK_DIRECTORY_SYNC_ALL_ACTOR,
        ).delete()
    except OperationalError, ProgrammingError:
        return


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0026_dingtalk_api_usage_bucket"),
        ("authentik_tasks_schedules", "0003_alter_schedule_managers"),
    ]

    operations = [
        migrations.RunPython(
            delete_stale_dingtalk_directory_sync_all_schedules,
            migrations.RunPython.noop,
        ),
    ]
