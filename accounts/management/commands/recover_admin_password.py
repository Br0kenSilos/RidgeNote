from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts import services
from accounts.identifiers import normalize_username
from accounts.models import AuditEvent, User


class Command(BaseCommand):
    help = "Securely reset the password for an existing RidgeNote administrator."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        username = normalize_username(options["username"])
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(username=username)
        except UserModel.DoesNotExist as exc:
            raise CommandError("No existing administrator account matches that username.") from exc

        if user.role != User.ROLE_ADMIN:
            raise CommandError("Password recovery can reset only an existing administrator.")

        password1 = getpass("New password: ")
        password2 = getpass("Confirm new password: ")
        if password1 != password2:
            raise CommandError("Passwords did not match.")
        validate_password(password2, user=user)

        with transaction.atomic():
            services.reset_user_password(
                target=user,
                new_password=password2,
                must_change_password=False,
                actor=None,
                event_type=AuditEvent.EVENT_CLI_ADMIN_PASSWORD_RECOVERED,
                source=AuditEvent.SOURCE_MANAGEMENT_COMMAND,
            )
        self.stdout.write(
            self.style.SUCCESS("Administrator password reset. Existing sessions invalidated.")
        )
