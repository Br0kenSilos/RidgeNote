from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("setup/", views.setup, name="setup"),
    path("invite/setup/", views.invite_setup, name="invite_setup"),
    path("invite/<str:raw_token>/", views.invite_entry, name="invite_entry"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("session/status/", views.session_status, name="session_status"),
    path("session/keepalive/", views.session_keepalive, name="session_keepalive"),
    path("session/expired/", views.session_expired_page, name="session_expired"),
    path("password/change-required/", views.forced_password_change, name="forced_password_change"),
    path("account/", views.account, name="account"),
    path("preferences/", views.preferences, name="preferences"),
    path("preferences/theme/", views.quick_set_theme, name="quick_set_theme"),
    path("tags/", views.tag_management, name="tag_management"),
    path("tags/create/", views.tag_create, name="tag_create"),
    path("tags/<int:tag_id>/rename/", views.tag_rename, name="tag_rename"),
    path("tags/<int:tag_id>/recolor/", views.tag_recolor, name="tag_recolor"),
    path("tags/<int:tag_id>/delete/", views.tag_delete, name="tag_delete"),
    path("tags/bulk-delete/", views.tag_bulk_delete, name="tag_bulk_delete"),
    path("admin/theme-calibration/", views.theme_calibration, name="theme_calibration"),
    path("admin/users/", views.user_list, name="user_list"),
    path("admin/users/new/", views.user_create, name="user_create"),
    path("admin/users/<int:user_id>/", views.user_detail, name="user_detail"),
    path("admin/users/<int:user_id>/role/", views.user_role, name="user_role"),
    path("admin/users/<int:user_id>/active/", views.user_active, name="user_active"),
    path("admin/users/<int:user_id>/email/", views.user_email, name="user_email"),
    path(
        "admin/users/<int:user_id>/schedule-deletion/",
        views.user_schedule_deletion,
        name="user_schedule_deletion",
    ),
    path(
        "admin/users/<int:user_id>/cancel-deletion/",
        views.user_cancel_deletion,
        name="user_cancel_deletion",
    ),
    path(
        "admin/users/<int:user_id>/purge/",
        views.user_purge,
        name="user_purge",
    ),
    path(
        "admin/users/<int:user_id>/reset-password/",
        views.user_password_reset,
        name="user_password_reset",
    ),
    path(
        "admin/users/<int:user_id>/invitation/reissue/",
        views.user_invitation_reissue,
        name="user_invitation_reissue",
    ),
    path(
        "admin/users/<int:user_id>/invitation/revoke/",
        views.user_invitation_revoke,
        name="user_invitation_revoke",
    ),
]
