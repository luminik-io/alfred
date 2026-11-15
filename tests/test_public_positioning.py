from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIRED_TAGLINE = "An autonomous engineering team that ships while you're away."
DESCRIPTION = "Claude Code, Codex or OpenCode on a schedule"
POSITIONING_ENTRYPOINTS = [
    ROOT / "site/src/pages/index.astro",
    ROOT / "site/src/content/docs/docs.mdx",
    ROOT / "site/src/pages/llms.txt.ts",
    ROOT / "site/src/pages/llms-full.txt.ts",
    ROOT / "site/src/layouts/MarketingLayout.astro",
]
PUBLIC_COPY = [
    ROOT / "README.md",
    *POSITIONING_ENTRYPOINTS,
    ROOT / "AGENTS.md",
    ROOT / "PRODUCT.md",
    ROOT / "docs/CONVERSATION.md",
    ROOT / "docs/DEMO.md",
    ROOT / "docs/DESKTOP_CLIENT.md",
    ROOT / "site/DESIGN.md",
    ROOT / "site/src/content/docs/guides/skills.md",
    ROOT / "clients/desktop/src/components/layout/AppShell.tsx",
]
ARCHIVE_NOTICE_SURFACES = [
    ROOT / "site/src/layouts/MarketingLayout.astro",
    ROOT / "site/src/components/ArchiveBanner.astro",
]

ISSUE_TEMPLATES = ROOT / ".github/ISSUE_TEMPLATE"


def test_public_entrypoints_describe_what_alfred_runs() -> None:
    for path in POSITIONING_ENTRYPOINTS:
        content = " ".join(path.read_text().split())
        assert DESCRIPTION in content, path
        assert "reviewed pull requests" in content, path
        assert RETIRED_TAGLINE not in content, path


def test_every_site_page_carries_the_archive_notice() -> None:
    for path in ARCHIVE_NOTICE_SURFACES:
        content = path.read_text()
        assert "Alfred is archived and no longer maintained." in content, path
        assert "github.com/luminik-io/alfred#status" in content, path


def test_readme_opens_with_what_alfred_runs_and_produces() -> None:
    readme = (ROOT / "README.md").read_text()
    opening = " ".join(readme.split("## Who it is for")[0].split())
    assert "Claude Code, Codex or OpenCode on a schedule" in opening
    assert "reviewed pull requests" in opening
    assert "decides what merges" in opening
    assert RETIRED_TAGLINE not in readme


def test_public_entrypoints_do_not_use_the_infrastructure_led_tagline() -> None:
    retired_phrases = [
        "run a supervised fleet of coding agents on your own machine.",
        "run a supervised fleet of claude code and codex agents on your own machine.",
        "supervised coding-agent fleets on your machine",
        "on your machine, behind an approval gate",
        "supervised local agent fleet",
        "supervised agent fleet",
        "supervised engineering fleet",
        "supervised coding agents",
        "one supervised team",
        "run a small engineering fleet yourself",
        "alfred is local-first",
        "coordination and supervision layer",
        "nothing proceeds without your say-so",
        "nothing single-repo ships without a go-ahead",
        "nothing runs without your approval",
        "waits for your go-ahead before anything is filed or run",
    ]
    for path in PUBLIC_COPY:
        content = path.read_text().casefold()
        for phrase in retired_phrases:
            assert phrase not in content, path


def test_issue_templates_match_the_current_product() -> None:
    bug = (ISSUE_TEMPLATES / "bug.yml").read_text()
    feature = (ISSUE_TEMPLATES / "feature.yml").read_text()
    question = (ISSUE_TEMPLATES / "question.yml").read_text()

    assert 'placeholder: "0.2.1"' not in bug
    assert "label: Operating system" in bug
    assert "alfred doctor" in bug
    assert "weekend-maintained" not in feature
    assert "vector DB" not in feature
    assert "the doc gets a star" not in question
