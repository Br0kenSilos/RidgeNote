"""Contract checks for deploy/README.md's manual-first structure, and
for the deeper operator/deployment material it deliberately delegates
to docs/RUNBOOK_DEPLOYMENT.md.

These assert concepts (ordering, presence of required concepts, absence
of unsafe patterns, and -- for guarantees split across both documents --
which document actually carries which part), not exact prose or heading
numbering -- the wording is free to evolve without breaking these tests,
as long as the underlying contract holds: the manual Compose/env/README
path is documented as the first-class, self-sufficient installation
procedure in Quick Deploy; the bootstrap helper is presented afterward
as an explicitly optional convenience; and the deep service-model/
migration/scheduler/PostgreSQL explanations Quick Deploy no longer
carries inline remain fully present and grounded in the comprehensive
runbook.
"""

import re
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).resolve().parent.parent.parent / "deploy"
DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"

# See core/tests/test_deployment_bundle.py's matching comment: `deploy/`
# (and, equally, `docs/`) is never copied into the built application
# image, so these contract checks are only meaningful from a full
# repository checkout. The module-level reads below must themselves be
# guarded -- not only the tests -- since an unguarded read would raise
# at collection time (before any `skipif` marker could apply) when
# `deploy/`/`docs/` are absent.
_DEPLOY_DIR_AVAILABLE = DEPLOY_DIR.is_dir() and DOCS_DIR.is_dir()

pytestmark = pytest.mark.skipif(
    not _DEPLOY_DIR_AVAILABLE,
    reason="deploy/ or docs/ is not present outside a full repository checkout",
)

README = (DEPLOY_DIR / "README.md").read_text() if _DEPLOY_DIR_AVAILABLE else ""
ENV_EXAMPLE = (DEPLOY_DIR / ".env.example").read_text() if _DEPLOY_DIR_AVAILABLE else ""
# The comprehensive operator/deployment reference. Quick Deploy
# (`deploy/README.md`, `README` above) intentionally moved deep
# service-model/topology/migration exposition here.
RUNBOOK = (DOCS_DIR / "RUNBOOK_DEPLOYMENT.md").read_text() if _DEPLOY_DIR_AVAILABLE else ""


def _section(text: str, start_pattern: str, end_pattern: str | None = None) -> str:
    """Returns the text from the first match of `start_pattern` up to (not
    including) the first later match of `end_pattern`, or to the end of
    `text` if `end_pattern` is None or not found. Heading level/numbering
    in `start_pattern`/`end_pattern` should stay as loose as the actual
    contract allows -- these checks protect meaning, not exact Markdown
    formatting."""
    start_match = re.search(start_pattern, text, re.IGNORECASE | re.MULTILINE)
    assert start_match, f"expected to find a heading matching {start_pattern!r}"
    start = start_match.start()
    if end_pattern is None:
        return text[start:]
    end_match = re.search(end_pattern, text[start:], re.IGNORECASE | re.MULTILINE)
    return text[start : start + end_match.start()] if end_match else text[start:]


def _heading_index(pattern: str) -> int:
    match = re.search(pattern, README, re.IGNORECASE | re.MULTILINE)
    assert match, f"expected to find a heading matching {pattern!r}"
    return match.start()


def _code_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:sh|dotenv)?\n(.*?)```", text, re.DOTALL)


def test_manual_installation_appears_before_helper_section():
    # The manual, step-by-step Quick Deploy path is a single ordered
    # list (no per-step H2 headings) -- match its first step directly
    # rather than requiring a heading level that no longer exists here.
    manual_start = _heading_index(r"1\.\s+\*\*Create the deployment directory\*\*")
    helper_start = _heading_index(r"^### Optional: automate steps")
    assert manual_start < helper_start


def test_helper_section_is_labeled_optional():
    helper_start = _heading_index(r"^### Optional: automate steps")
    nearby = README[helper_start : helper_start + 300]
    assert "Optional" in nearby
    assert "Review the script before running it" in nearby


def test_helper_is_never_described_as_required():
    assert "never a requirement" in README.lower() or "entirely optional" in README.lower()


def test_minimal_three_file_bundle_is_documented_as_sufficient():
    # "Bundle contents" is no longer a dedicated section -- the
    # three-file sufficiency guarantee now lives directly in
    # "Before you begin", where an installer reads it before starting.
    section = _section(README, r"^## Before you begin", r"^## Quick Deploy")
    assert "docker-compose.yml" in section
    assert ".env.example" in section
    assert "README.md" in section
    assert "exactly these three files, and nothing else" in section


def test_optional_helper_bundle_is_documented():
    section = _section(README, r"^## Before you begin", r"^## Quick Deploy")
    assert "setup-deploy-host.sh" in section
    assert "optional fourth file" in section
    assert "convenience, never a requirement" in section


def test_manual_path_covers_required_steps():
    required_concepts = [
        r"cp \.env\.example \.env",  # .env creation
        r"openssl rand",  # secret generation
        r"mkdir -p data/postgres backups/postgres",  # persistent directories
        r"entrypoint /usr/bin/id",  # PostgreSQL UID/GID discovery
        r"sudo chown",  # ownership
        r"docker compose config --quiet",  # Compose validation
        r"docker compose pull",  # image pull
        r"docker compose up -d\n",  # single-command startup (automatic migrations)
        r"docker compose logs -f",  # watch startup/migration logs
        r"/health/",  # health check
        r"/setup/",  # administrator bootstrap
    ]
    for pattern in required_concepts:
        assert re.search(pattern, README), f"missing required manual-path step: {pattern}"


def test_manual_path_does_not_require_a_separate_manual_migration_step():
    install_start = _heading_index(r"1\.\s+\*\*Create the deployment directory\*\*")
    install_end = _heading_index(r"10\.\s+\*\*Create the first administrator")
    install_section = README[install_start:install_end]
    # The leading numbered install procedure must not instruct the
    # operator to run a migration command themselves -- automatic
    # startup migrations (grounded, in depth, in the full runbook --
    # see test_automatic_startup_migrations_section_present_and_grounded)
    # must be relied on instead.
    assert "manage.py migrate" not in install_section
    assert "docker compose up -d postgres" not in install_section
    assert "docker compose up -d web scheduler" not in install_section
    assert "applies any pending database migrations automatically" in install_section
    assert re.search(r"no separate manual migration\s+step", install_section)


def test_automatic_startup_migrations_section_present_and_grounded():
    # Moved from Quick Deploy to the comprehensive operator/deployment
    # reference -- Quick Deploy states the guarantee (previous test);
    # this is where the full mechanism is explained and grounded.
    section = _section(RUNBOOK, r"^## 4a\. Automatic startup migrations", r"^## 5\.")
    assert "advisory lock" in section
    assert "migrate --noinput" in section
    assert re.search(r"60-90\s+seconds", section)
    assert "do not make every upgrade risk-free" in section


def test_no_remote_pipe_to_shell_pattern():
    assert not re.search(r"curl\s+.*\|\s*(sh|bash)\b", README)
    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    helper = (scripts_dir / "setup-deploy-host.sh").read_text()
    assert not re.search(r"curl\s+.*\|\s*(sh|bash)\b", helper)


def test_no_chmod_777_instructed():
    # "777" may appear in prose warning against it (e.g. "never use
    # chmod 777") -- it must never appear as an actual command inside a
    # fenced shell code block.
    for block in _code_blocks(README):
        assert not re.search(r"chmod\s+777\b", block)


def test_no_universal_hardcoded_postgres_uid_gid():
    # 999:999 must never appear as a literal instructed value inside a
    # command block -- only real discovery commands are permitted there.
    for block in _code_blocks(README):
        assert "999:999" not in block


def test_env_example_distinguishes_required_from_optional_settings():
    # The file must group settings so an installer can tell at a glance
    # what needs deliberate attention (REQUIRED, no safe default) from
    # what already has a safe default (OPTIONAL) -- exactly two
    # top-level categories, not an ambiguous third tier.
    for marker in ("REQUIRED", "OPTIONAL", "SMTP invitation delivery"):
        assert marker in ENV_EXAMPLE


def test_env_example_still_documents_loopback_host():
    assert "127.0.0.1" in ENV_EXAMPLE


def test_env_example_has_no_duplicate_variable_assignments():
    names = re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE, re.MULTILINE)
    assert len(names) == len(set(names)), "duplicate variable declared in deploy/.env.example"


def test_env_example_lines_are_well_formed():
    # Every active (non-blank, non-comment) line must be a plain
    # `VAR=value` assignment -- catches structural corruption or stray
    # prose without imposing any ceiling on legitimate growth.
    for line in ENV_EXAMPLE.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert re.match(r"^[A-Z][A-Z0-9_]*=", stripped), f"malformed line: {stripped!r}"


def test_image_tag_guidance_is_neutral():
    tags_start = _heading_index(r"^## Image tags")
    section = README[tags_start : tags_start + 600].lower()
    assert "latest" in section
    # Must not tell the installer a moving tag is unsafe/inappropriate.
    assert "unsafe" not in section
    assert "not recommended" not in section
    assert "not appropriate" not in section


def test_scheduler_explanation_is_present_and_grounded_in_code():
    # Moved to the comprehensive operator/deployment reference -- the
    # introduction (section 1a) and the detailed operational section
    # (section 8) together are "the scheduler explanation" now.
    section = _section(RUNBOOK, r"^### 1a\. Service overview", r"^### 8a\.")
    assert "run_purge_scheduler" in section
    assert "RIDGENOTE_PURGE_ENABLED" in section
    assert "RIDGENOTE_PURGE_INTERVAL_SECONDS" in section
    # Must state the verified disabled-by-default behavior, not merely
    # what it does when enabled.
    assert "false" in section
    assert "nothing" in section.lower()


def test_postgres_explanation_is_present():
    # No dedicated "Why PostgreSQL?" heading exists by design -- the
    # rationale is folded into the service-overview bullet for
    # `postgres`. Assert the concept, not a heading.
    section = _section(RUNBOOK, r"^### 1a\. Service overview", r"^## 2\.")
    assert re.search(r"PostgreSQL is RidgeNote's only", section)
    assert re.search(r"no separate cache,\s+queue,\s+or\s+search service", section)


def test_service_overview_section_present():
    # Heading text is stable ("Service overview"); its numbering
    # prefix ("1a.") is incidental formatting, not part of the
    # contract, so the pattern doesn't require it.
    assert re.search(r"^#{2,3}.*Service overview", RUNBOOK, re.MULTILINE)


def test_compose_has_concise_service_comments():
    compose = (DEPLOY_DIR / "docker-compose.yml").read_text()
    assert "RidgeNote web application" in compose
    assert "purge" in compose.lower()
    assert "RidgeNote database" in compose
    # Concise, not paragraph-length: no comment line should run long.
    for line in compose.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            assert len(stripped) < 100, f"comment line too long: {stripped!r}"
