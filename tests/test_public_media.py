from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_tour_is_fixture_only() -> None:
    tour_spec = ROOT / "clients/desktop/e2e/public-tour.spec.ts"
    capture_script = ROOT / "clients/desktop/scripts/capture-public-tour.mjs"
    homepage = (ROOT / "site/src/pages/index.astro").read_text()

    assert tour_spec.is_file()
    assert capture_script.is_file()
    tour_source = tour_spec.read_text()
    assert "installAlfredApi" in tour_source
    assert "assertAlfredApiComplete" in tour_source
    assert 'localStorage.setItem("alfred-theme", "light")' in tour_source
    assert 'localStorage.setItem("alfred-theme", "dark")' not in tour_source
    assert "sample-data fixture" in homepage
    assert (ROOT / "docs/media/alfred-tour.mp4").read_bytes() == (
        ROOT / "site/public/media/alfred-tour.mp4"
    ).read_bytes()
    assert (ROOT / "docs/media/alfred-tour-poster.png").read_bytes() == (
        ROOT / "site/public/media/alfred-tour-poster.png"
    ).read_bytes()


def test_public_docs_do_not_ship_live_operator_screenshots() -> None:
    live_media = ROOT / "docs/images/real"
    tracked_images = list(live_media.glob("*")) if live_media.exists() else []

    assert tracked_images == []
    documents = [ROOT / "README.md"]
    documents.extend((ROOT / "docs").rglob("*.md"))
    documents.extend((ROOT / "site").rglob("*.md"))
    for path in documents:
        assert "images/real/" not in path.read_text()

    retired_media = [
        ROOT / "docs/images/ask-dark.png",
        ROOT / "docs/images/ask-light.png",
        ROOT / "docs/images/setup-dark.png",
        ROOT / "docs/images/setup-light.png",
        ROOT / "docs/media/alfred-tour.webp",
        ROOT / "site/public/proof/card-shipped-summary.png",
    ]
    assert not any(path.exists() for path in retired_media)
