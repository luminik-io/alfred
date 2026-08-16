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
    ROOT / "PRODUCT.md",
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
    ]
    for path in PUBLIC_COPY:
        content = path.read_text().casefold()
        for phrase in retired_phrases:
            assert phrase not in content, path
