from django.db import migrations, models
from django.db.models.functions import Lower, Trim


def normalize_username(value: str) -> str:
    return value.strip().lower()


def normalize_display_name(value: str) -> str:
    return value.strip()


def populate_display_names_and_normalize_usernames(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    pending_updates = []
    normalized_to_ids: dict[str, list[int]] = {}

    for user in User.objects.order_by("id"):
        trimmed_username = user.username.strip()
        normalized_username = normalize_username(user.username)
        display_name = normalize_display_name(user.display_name)

        if not normalized_username:
            raise RuntimeError(
                "Cannot normalize username for accounts.User "
                f"id={user.id}: the trimmed username is blank."
            )

        normalized_to_ids.setdefault(normalized_username, []).append(user.id)
        pending_updates.append((user, normalized_username, display_name, trimmed_username))

    collisions = {username: ids for username, ids in normalized_to_ids.items() if len(ids) > 1}
    if collisions:
        collision_summary = ", ".join(
            f"{username}: ids {ids}" for username, ids in sorted(collisions.items())
        )
        raise RuntimeError(
            "Cannot apply accounts username normalization because normalized username collisions "
            f"were detected: {collision_summary}."
        )

    for user, normalized_username, display_name, trimmed_username in pending_updates:
        user.username = normalized_username
        if not display_name:
            user.display_name = trimmed_username
        else:
            user.display_name = display_name
        user.save(update_fields=["username", "display_name"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="display_name",
            field=models.CharField(blank=True, default="", max_length=150),
        ),
        migrations.RunPython(
            populate_display_names_and_normalize_usernames,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(username=Lower(Trim("username"))),
                name="accounts_user_username_canonical_ck",
            ),
        ),
    ]
