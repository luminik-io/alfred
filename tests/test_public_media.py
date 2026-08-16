import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", header[16:24])


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


def test_public_gallery_is_fixture_only_light_mode() -> None:
    gallery_spec = ROOT / "clients/desktop/e2e/public-gallery.spec.ts"
    gallery_config = ROOT / "clients/desktop/playwright.gallery.config.ts"
    capture_script = ROOT / "clients/desktop/scripts/capture-public-gallery.mjs"
    desktop_package = (ROOT / "clients/desktop/package.json").read_text()

    assert gallery_spec.is_file()
    assert gallery_config.is_file()
    assert capture_script.is_file()
    gallery_source = gallery_spec.read_text()
    config_source = gallery_config.read_text()
    assert "installAlfredApi" in gallery_source
    assert "assertAlfredApiComplete" in gallery_source
    assert 'localStorage.setItem("alfred-theme", "light")' in gallery_source
    assert 'localStorage.setItem("alfred-theme", "dark")' not in gallery_source
    assert "width: 1440" in config_source
    assert "height: 862" in config_source
    assert "scale=1270:760:flags=lanczos" in capture_script.read_text()
    assert '"capture:gallery"' in desktop_package

    names = (
        "alfred-gallery-work.png",
        "alfred-gallery-agents.png",
        "alfred-gallery-approval.png",
    )
    assert {path.name for path in (ROOT / "docs/media/gallery").glob("*.png")} == set(names)
    assert {path.name for path in (ROOT / "site/public/media/gallery").glob("*.png")} == set(names)
    for name in names:
        docs_asset = ROOT / "docs/media/gallery" / name
        site_asset = ROOT / "site/public/media/gallery" / name
        assert docs_asset.read_bytes() == site_asset.read_bytes()
        assert _png_size(docs_asset) == (1270, 760)


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
        ROOT / "docs/images/demo.gif",
        ROOT / "docs/images/ask-dark.png",
        ROOT / "docs/images/ask-light.png",
        ROOT / "docs/images/setup-dark.png",
        ROOT / "docs/images/setup-light.png",
        ROOT / "docs/media/alfred-tour.webp",
        ROOT / "site/public/proof/card-shipped-summary.png",
    ]
    assert not any(path.exists() for path in retired_media)

    for path in documents:
        assert "docs/images/demo.gif" not in path.read_text()
        assert "images/demo.gif" not in path.read_text()
