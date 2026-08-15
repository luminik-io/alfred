"""Keep the public site proof limited to public repository evidence."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def test_public_pages_use_the_public_repository_proof() -> None:
    for relative in ("src/pages/index.astro", "src/pages/impact.astro"):
        page = (SITE / relative).read_text(encoding="utf-8")

        assert 'from "../data/impact-proof.json"' in page
        assert "luminik-product-proof" not in page


def test_private_repository_aggregate_emitter_is_not_shipped() -> None:
    package = json.loads((SITE / "package.json").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github/workflows/site.yml").read_text(encoding="utf-8")

    assert "proof:product" not in package["scripts"]
    assert "proof:product" not in workflow
    assert "ALFRED_PRODUCT_PROOF" not in workflow
    assert not (SITE / "scripts/build-product-proof.mjs").exists()
    assert not (SITE / "src/data/luminik-product-proof.json").exists()
    assert not (SITE / "public/proof/slack-shipped-summary.png").exists()


def test_public_copy_does_not_describe_private_repository_aggregates() -> None:
    checked = [
        SITE / "src/pages/index.astro",
        SITE / "src/pages/impact.astro",
        ROOT / "docs/SHIPPED_EMITTER.md",
    ]
    copy = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    assert "private Luminik" not in copy
    assert "Luminik product setup" not in copy
    assert "ALFRED_PRODUCT_PROOF_REPOS" not in copy


def test_agent_readable_site_surfaces_use_the_product_truth() -> None:
    paths = [
        SITE / "src/layouts/MarketingLayout.astro",
        SITE / "src/pages/agents.md.ts",
        SITE / "src/pages/llms.txt.ts",
        SITE / "src/pages/llms-full.txt.ts",
    ]
    copy = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "autonomous coding agents" not in copy.lower()
    assert "while you are away" not in copy.lower()
    assert "supervised" in copy.lower()


def test_llms_surfaces_read_the_docs_home_entry() -> None:
    for name in ("llms.txt.ts", "llms-full.txt.ts"):
        page = (SITE / "src/pages" / name).read_text(encoding="utf-8")

        assert 'd.id === "docs"' in page
