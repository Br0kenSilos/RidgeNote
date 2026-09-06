import django.db.models.functions.text
from django.db import migrations, models


def normalize_email(value: str) -> str:
    return value.strip().lower()


def canonicalize_and_check_email_collisions(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    pending_updates = []
    canonical_to_ids: dict[str, list[int]] = {}

    for user in User.objects.order_by("id"):
        canonical_email = normalize_email(user.email)
        if canonical_email:
            canonical_to_ids.setdefault(canonical_email, []).append(user.id)
        if canonical_email != user.email:
            pending_updates.append((user, canonical_email))

    collision_groups = sorted(
        (ids for ids in canonical_to_ids.values() if len(ids) > 1),
        key=lambda ids: ids[0],
    )
    if collision_groups:
        collision_summary = "; ".join(f"user ids {ids}" for ids in collision_groups)
        raise RuntimeError(
            "Cannot apply accounts email normalization because canonical email "
            f"collisions were detected among: {collision_summary}. Resolve these "
            "duplicate accounts manually before retrying this migration."
        )

    for user, canonical_email in pending_updates:
        user.email = canonical_email
        user.save(update_fields=["email"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_user_tag_preferences"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(
            canonicalize_and_check_email_collisions,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("email", ""),
                    (
                        "email",
                        django.db.models.functions.text.Lower(
                            django.db.models.functions.text.Trim("email")
                        ),
                    ),
                    _connector="OR",
                ),
                name="accounts_user_email_canonical_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                condition=models.Q(("email", ""), _negated=True),
                fields=("email",),
                name="accounts_user_email_nonblank_uq",
            ),
        ),
    ]
