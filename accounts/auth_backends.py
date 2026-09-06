from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from accounts.identifiers import normalize_username


class CanonicalUsernameModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        normalized_username = normalize_username(username)

        try:
            user = UserModel._default_manager.get(username=normalized_username)
        except UserModel.DoesNotExist:
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def user_can_authenticate(self, user) -> bool:
        return super().user_can_authenticate(user) and user.has_completed_setup
