from django.db import migrations, models


def backfill_setup_completed_at_from_date_joined(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(setup_completed_at__isnull=True).update(
        setup_completed_at=models.F("date_joined")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_user_email_normalization_and_uniqueness"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="setup_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_setup_completed_at_from_date_joined,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
