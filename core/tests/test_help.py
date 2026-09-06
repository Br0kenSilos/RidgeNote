"""In-App Help -- Foundation, Canonical Route, No-JS Fallback, and
Content Population.

Covers the real content-delivery foundation: a canonical, authenticated
`/help/` route, and one topic partial per major Help topic
(`core/templates/core/help/_<topic>.html`), included by the shared
`_body.html` layout wrapper and reused, unchanged, by both the
standalone page and the global dialog (`core/templates/base.html`).
The canonical page always renders every topic, unhidden, as one
complete linear document -- only the JS-enhanced dialog ever shows a
single selected topic at a time (see `app.ts`'s `initHelpTopics()`),
which these backend tests cannot exercise directly; they instead
confirm every topic and its canonical-fragment links are present and
correctly gated in the server-rendered markup both contexts share.
Every topic has real content;
these tests assert structure and critical semantics (headings, stable
ids, key concepts each topic must mention, cross-links), never whole
paragraphs verbatim, so future content wording can evolve without the
tests becoming brittle.
"""

import re

import pytest
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

PASSWORD = "LongUniquePassword123!"


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


def authenticated_client(user):
    from accounts import services as account_services

    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


@pytest.mark.django_db
def test_help_page_requires_authentication():
    response = Client().get(reverse("help"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))


@pytest.mark.django_db
def test_help_page_available_to_ordinary_authenticated_user():
    user = create_account("help-ordinary-user")

    response = authenticated_client(user).get(reverse("help"))

    assert response.status_code == 200


@pytest.mark.django_db
def test_help_page_available_to_admin():
    admin = create_account("help-admin-user", role=User.ROLE_ADMIN)

    response = authenticated_client(admin).get(reverse("help"))

    assert response.status_code == 200


@pytest.mark.django_db
def test_help_page_shows_heading_and_toc():
    user = create_account("help-toc-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()
    help_url = reverse("help")

    assert "<h1>Help</h1>" in content
    assert 'class="help-toc"' in content
    # TOC hrefs are canonical /help/#<id>
    # fragments (not bare #<id>) -- real navigation destinations even
    # when rendered inside the dialog, per the modal-topic-switching
    # architecture (app.ts intercepts these specifically).
    assert f'href="{help_url}#help-introduction"' in content
    assert f'href="{help_url}#help-getting-started"' in content
    assert f'href="{help_url}#help-library-backup"' in content
    assert "data-help-topic-link" in content


@pytest.mark.django_db
def test_help_page_introduction_appears_first_before_getting_started():
    # Introduction is the
    # first topic (source order), which is also what makes it the
    # modal's default selected topic (app.ts's DEFAULT_HELP_TOPIC).
    user = create_account("help-introduction-order-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    assert content.index('data-help-topic="help-introduction"') < content.index(
        'data-help-topic="help-getting-started"'
    )


@pytest.mark.django_db
def test_help_page_uses_two_column_layout_structure():
    # .help-toc (left/nav)
    # and .help-article (right/content) are distinct siblings inside
    # one shared .help-layout wrapper, reused identically by the
    # canonical page and the dialog.
    user = create_account("help-layout-structure-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    assert 'class="help-layout"' in content
    assert 'class="help-toc"' in content
    assert 'class="help-article"' in content
    # The TOC nav must precede the article pane in source order.
    assert content.index('class="help-toc"') < content.index('class="help-article"')


@pytest.mark.django_db
def test_help_page_representative_inline_jump_links_point_to_valid_section_ids():
    # Inline cross-reference links inside topic prose (e.g. Notes ->
    # Tags, Trash and Recovery -> Library Backup and Restore) prove the
    # jump-link/topic-switching architecture works: each inline link's
    # href must resolve to a real, existing section id on the same
    # page, using the same canonical /help/#<id> destination and
    # data-help-topic-link marker as the TOC.
    user = create_account("help-jumplink-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()
    help_url = reverse("help")

    assert f'href="{help_url}#help-tags"' in content
    assert f'href="{help_url}#help-library-backup"' in content
    assert 'id="help-tags"' in content
    assert 'id="help-library-backup"' in content


@pytest.mark.django_db
def test_help_page_topic_sections_carry_stable_data_help_topic_attribute():
    # Each topic partial's own <section> owns a data-help-topic
    # attribute matching its heading id exactly -- the mechanism
    # app.ts's showHelpTopic() uses to select one topic at a time
    # inside the dialog.
    user = create_account("help-topic-attr-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    for topic_id in [
        "help-introduction",
        "help-getting-started",
        "help-notes",
        "help-folders",
        "help-tags",
        "help-search",
        "help-trash-recovery",
        "help-library-backup",
        "help-account-preferences",
    ]:
        assert f'data-help-topic="{topic_id}"' in content


@pytest.mark.django_db
def test_help_page_shows_all_nine_ordinary_sections():
    user = create_account("help-sections-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    for section_id in [
        "help-introduction",
        "help-getting-started",
        "help-notes",
        "help-folders",
        "help-tags",
        "help-search",
        "help-trash-recovery",
        "help-library-backup",
        "help-account-preferences",
    ]:
        assert f'id="{section_id}"' in content


@pytest.mark.django_db
def test_help_page_hides_administrator_section_for_ordinary_user():
    user = create_account("help-hide-admin-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    assert "help-administrators" not in content
    assert "For Administrators" not in content


@pytest.mark.django_db
def test_help_page_shows_administrator_section_for_admin():
    admin = create_account("help-show-admin-user", role=User.ROLE_ADMIN)

    response = authenticated_client(admin).get(reverse("help"))
    content = response.content.decode()

    assert 'id="help-administrators"' in content
    assert "For Administrators" in content
    assert 'data-help-topic="help-administrators"' in content


@pytest.mark.django_db
def test_home_page_dialog_hides_administrator_topic_for_ordinary_user():
    # Server-side gating must hold for the dialog's copy of the shared
    # content too, not just the canonical page -- the admin topic must
    # be genuinely absent from the DOM, not merely hidden client-side.
    user = create_account("help-dialog-hide-admin-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    assert "help-administrators" not in content
    assert "For Administrators" not in content


@pytest.mark.django_db
def test_home_page_dialog_shows_administrator_topic_for_admin():
    admin = create_account("help-dialog-show-admin-user", role=User.ROLE_ADMIN)

    response = authenticated_client(admin).get(reverse("home"))
    content = response.content.decode()

    assert 'id="help-administrators"' in content
    assert 'data-help-topic="help-administrators"' in content


@pytest.mark.django_db
def test_help_page_does_not_render_the_global_help_dialog_a_second_time():
    # The canonical page suppresses the global dialog entirely
    # (`{% block help_dialog %}{% endblock %}`) rather than rendering
    # two independent copies of the shared content/section IDs on the
    # same document.
    user = create_account("help-no-duplicate-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    assert content.count('id="help-notes"') == 1
    assert 'id="help-panel"' not in content


@pytest.mark.django_db
def test_home_page_still_renders_exactly_one_copy_of_shared_help_content():
    # Ordinary pages keep the global dialog (with the shared content
    # inside it) and are unaffected by the canonical page's suppression.
    user = create_account("help-home-dialog-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    assert content.count('id="help-notes"') == 1
    assert 'id="help-panel"' in content


@pytest.mark.django_db
def test_home_page_help_trigger_is_a_real_link_with_no_js_fallback():
    user = create_account("help-trigger-link-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    help_url = reverse("help")
    assert f'href="{help_url}"' in content
    assert "data-help-toggle" in content
    assert 'aria-label="Help"' in content
    assert 'aria-expanded="false"' in content
    assert 'aria-controls="help-panel"' in content


# ---------------------------------------------------------------------------
# Help Content Population
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_help_page_no_scaffold_placeholder_remains():
    user = create_account("help-no-placeholder-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    assert "Content coming soon." not in content


@pytest.mark.django_db
def test_help_page_topics_carry_subheadings():
    # content uses <h3> subheadings within each topic, per the
    # intended content-granularity rule (subheadings within a topic
    # rather than new top-level topics). The Introduction topic
    # is deliberately prose-only
    # (no <h3>s of its own), so this floor is set against the other
    # eight ordinary topics rather than all nine.
    user = create_account("help-subheadings-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    assert content.count("<h3>") >= 8  # at least one per non-Introduction ordinary topic


@pytest.mark.parametrize(
    "topic_id,expected_phrase",
    [
        ("help-folders", "Unfiled"),
        ("help-tags", "Tag manager"),
        ("help-search", "Full Search"),
        ("help-trash-recovery", "30"),  # owner-visible recovery window, in days
        ("help-trash-recovery", "90"),  # final purge window, in days
        ("help-library-backup", "Restore previous library backup"),
        ("help-library-backup", "replaces your current library"),
        ("help-account-preferences", "timezone"),
    ],
)
@pytest.mark.django_db
def test_help_page_topic_mentions_critical_concept(topic_id, expected_phrase):
    user = create_account(f"help-concept-{topic_id}-{abs(hash(expected_phrase)) % 10000}")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    section_start = content.index(f'data-help-topic="{topic_id}"')
    next_section = content.find("data-help-topic=", section_start + 1)
    section_html = content[section_start : next_section if next_section != -1 else None]

    assert expected_phrase in section_html


@pytest.mark.django_db
def test_help_notes_topic_documents_automatic_saving():
    # the shipped copy reads
    # "RidgeNote saves your changes automatically as you work" -- kept
    # as-is, since it is accurate and natural. This checks the autosave
    # *concept* with a narrow regex tolerant of that grammatical form,
    # not the literal three-word phrase "save automatically", which no
    # wording in this topic has ever contained.
    user = create_account("help-notes-autosave-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    section_start = content.index('data-help-topic="help-notes"')
    next_section = content.find("data-help-topic=", section_start + 1)
    section_html = content[section_start : next_section if next_section != -1 else None]

    assert re.search(r"\bsaves?\b[^.]{0,20}\bautomatically\b", section_html)


@pytest.mark.django_db
def test_help_page_administrator_topic_mentions_schedule_deletion_and_purge():
    admin = create_account("help-admin-concept-user", role=User.ROLE_ADMIN)

    response = authenticated_client(admin).get(reverse("help"))
    content = response.content.decode()

    section_start = content.index('data-help-topic="help-administrators"')
    section_html = content[section_start:]

    assert "Schedule deletion" in section_html
    assert "Permanently purge" in section_html
    assert "7-day" in section_html or "7 day" in section_html


@pytest.mark.django_db
def test_help_administrators_topic_defines_scope_without_actionable_instructions():
    # naming an operator-domain
    # technology (Docker, SMTP, etc.) to redirect the reader elsewhere is
    # not, by itself, operator material -- the actual contract this topic
    # must meet is that it states server/deployment work is out of scope,
    # points the reader to operator documentation, and never gives an
    # actionable instruction (a literal command or configuration value)
    # to follow. Vocabulary words are not banned.
    admin = create_account("help-admin-no-operator-user", role=User.ROLE_ADMIN)

    response = authenticated_client(admin).get(reverse("help"))
    content = response.content.decode()

    section_start = content.index('data-help-topic="help-administrators"')
    next_section = content.find("</div>", section_start)
    section_html = content[section_start:next_section]

    assert "outside the scope of in-app Help" in section_html
    assert "operator documentation" in section_html
    # No actionable instruction: Help has no <code>/<pre> command markers
    # and no RIDGENOTE_ environment-variable-style tokens anywhere today.
    assert "<code" not in section_html
    assert "<pre" not in section_html
    assert "RIDGENOTE_" not in section_html


@pytest.mark.django_db
def test_help_trash_recovery_topic_does_not_expose_internal_marker_name():
    user = create_account("help-no-marker-leak-user")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    assert "is_recovery_folder" not in content


@pytest.mark.django_db
def test_help_page_cross_links_reference_multiple_distinct_topics():
    # A loose density check: content should link to several
    # different topics, not just a couple of scaffold examples.
    user = create_account("help-cross-link-density-user")
    help_url = reverse("help")

    response = authenticated_client(user).get(reverse("help"))
    content = response.content.decode()

    linked_targets = {
        topic_id
        for topic_id in [
            "help-introduction",
            "help-getting-started",
            "help-notes",
            "help-folders",
            "help-tags",
            "help-search",
            "help-trash-recovery",
            "help-library-backup",
            "help-account-preferences",
        ]
        # count > 1 means: TOC entry plus at least one inline cross-link
        if content.count(f'href="{help_url}#{topic_id}"') > 1
    }
    assert len(linked_targets) >= 5


def _section_html(content, topic_id):
    section_start = content.index(f'data-help-topic="{topic_id}"')
    next_section = content.find("data-help-topic=", section_start + 1)
    return content[section_start : next_section if next_section != -1 else None]


def _normalize_whitespace(html):
    # Template line-wrapping inserts newlines/indentation inside prose
    # that reads as a single sentence in the browser -- collapse runs of
    # whitespace so a verbatim-wording check isn't sensitive to how the
    # template happens to be line-wrapped.
    return re.sub(r"\s+", " ", html).strip()


@pytest.mark.django_db
def test_help_account_preferences_documents_appearance():
    # Account and Preferences includes an
    # Appearance subsection alongside Timezone/Tag preferences -- no new
    # topic, no new navigation entry. This asserts the required content
    # is present: all eight theme names and the exact
    # account-wide persistence wording.
    user = create_account("help-appearance-user")

    response = authenticated_client(user).get(reverse("help"))
    section_html = _section_html(response.content.decode(), "help-account-preferences")

    assert "Appearance" in section_html
    for theme_name in [
        "Warm Light",
        "Dark",
        "Glacier",
        "Granite",
        "Alpine Mist",
        "Blue Dusk",
        "Midnight Ridge",
        "Nightfall",
    ]:
        assert theme_name in section_html

    assert (
        "Theme is saved to your account and applies across devices. "
        "The most recent successful theme change becomes the current "
        "theme everywhere."
    ) in _normalize_whitespace(section_html)


@pytest.mark.django_db
def test_help_account_preferences_mentions_login_fixed_dark_appearance():
    user = create_account("help-login-appearance-user")

    response = authenticated_client(user).get(reverse("help"))
    section_html = _section_html(response.content.decode(), "help-account-preferences")

    assert "Sign In" in section_html
    assert "dark" in section_html.lower()


@pytest.mark.django_db
def test_help_account_preferences_appearance_has_no_stale_wording():
    # Help's Appearance content describes the current theme system
    # directly -- this guards against ever reintroducing the binary
    # toggle framing, the retired icon name, or a device-specific theme
    # description the current content explicitly does not describe.
    user = create_account("help-appearance-stale-user")

    response = authenticated_client(user).get(reverse("help"))
    section_html = _section_html(response.content.decode(), "help-account-preferences").lower()

    for forbidden in ["contrast", "toggle", "only two themes", "light or dark"]:
        assert forbidden not in section_html


@pytest.mark.django_db
def test_help_getting_started_preferences_summary_mentions_appearance():
    user = create_account("help-getting-started-appearance-user")

    response = authenticated_client(user).get(reverse("help"))
    section_html = _section_html(response.content.decode(), "help-getting-started")

    assert "appearance" in section_html.lower()
