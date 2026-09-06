# Widen the persisted `theme` field and its
# DB check constraint to accept the six additional production
# themes (Glacier, Granite, Alpine Mist, Blue Dusk, Midnight Ridge,
# Nightfall) alongside the existing Warm Light/Dark. `max_length=20`
# already covers every new value (the longest, "midnight-ridge", is 14
# characters); no data migration is needed since every existing
# `warm-light`/`dark` row remains valid under the widened constraint.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_user_theme"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="theme",
            field=models.CharField(
                choices=[
                    ("warm-light", "Warm Light"),
                    ("dark", "Dark"),
                    ("glacier", "Glacier"),
                    ("granite", "Granite"),
                    ("alpine-mist", "Alpine Mist"),
                    ("blue-dusk", "Blue Dusk"),
                    ("midnight-ridge", "Midnight Ridge"),
                    ("nightfall", "Nightfall"),
                ],
                default="warm-light",
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="user",
            name="accounts_user_theme_valid_ck",
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "theme__in",
                        (
                            "warm-light",
                            "dark",
                            "glacier",
                            "granite",
                            "alpine-mist",
                            "blue-dusk",
                            "midnight-ridge",
                            "nightfall",
                        ),
                    )
                ),
                name="accounts_user_theme_valid_ck",
            ),
        ),
    ]
