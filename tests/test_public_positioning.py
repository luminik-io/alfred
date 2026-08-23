from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSITIONING = "An autonomous engineering team that ships while you're away."
AUTONOMY_COPY = "Alfred keeps working without you at the keyboard."
CONTROL_COPY = "You approve risky actions and decide what merges."
POSITIONING_ENTRYPOINTS = [
    ROOT / "README.md",
    ROOT / "site/src/pages/index.astro",
    ROOT / "site/src/content/docs/docs.mdx",
    ROOT / "site/src/pages/llms.txt.ts",
    ROOT / "site/src/pages/llms-full.txt.ts",
    ROOT / "site/src/layouts/MarketingLayout.astro",
    ROOT / "site/scripts/generate-og.mjs",
]
PUBLIC_COPY = [
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
AUTONOMY_ENTRYPOINTS = [
    ROOT / "README.md",
    ROOT / "site/src/pages/index.astro",
    ROOT / "site/src/content/docs/docs.mdx",
    ROOT / "site/scripts/generate-og.mjs",
]

ISSUE_TEMPLATES = ROOT / ".github/ISSUE_TEMPLATE"


def test_public_entrypoints_use_the_outcome_led_positioning() -> None:
    for path in POSITIONING_ENTRYPOINTS:
        assert POSITIONING in path.read_text(), path


def test_supporting_copy_pairs_autonomy_with_human_control() -> None:
    for path in AUTONOMY_ENTRYPOINTS:
        content = " ".join(path.read_text().split())
        assert AUTONOMY_COPY in content, path
        assert CONTROL_COPY in content, path


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
