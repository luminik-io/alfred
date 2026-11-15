"""Workflow Node pins stay in step and satisfy the packages' engine floors."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = [ROOT / "site/package.json", ROOT / "clients/desktop/package.json"]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _workflow_pins() -> dict[str, str | None]:
    """Map each actions/setup-node step to its node-version pin, or None if it has none."""
    pins: dict[str, str | None] = {}
    for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
        lines = workflow.read_text().splitlines()
        for index, line in enumerate(lines):
            if not re.search(r"uses:\s*actions/setup-node@", line):
                continue
            step_indent = len(line) - len(line.lstrip(" -"))
            pin = None
            for following in lines[index + 1 :]:
                stripped = following.strip()
                indent = len(following) - len(following.lstrip(" "))
                if stripped.startswith("- ") and indent < step_indent:
                    break
                match = re.match(r"node-version:\s*['\"]?([^'\"\s#]+)", stripped)
                if match:
                    pin = match.group(1)
                    break
            pins[f"{workflow.name}:{index + 1}"] = pin
    return pins


def test_every_setup_node_step_has_a_pin() -> None:
    pins = _workflow_pins()
    assert pins
    assert all(pins.values()), pins


def test_every_workflow_pins_the_same_node_version() -> None:
    pins = _workflow_pins()
    assert len(set(pins.values())) == 1, pins


def test_node_pin_meets_each_package_engine_floor() -> None:
    pinned = _version(str(next(iter(_workflow_pins().values()))))
    for package in PACKAGES:
        floor = json.loads(package.read_text())["engines"]["node"]
        assert floor.startswith(">="), package
        assert pinned >= _version(floor[2:]), package


def test_node_pin_is_new_enough_for_current_astro_packages() -> None:
    # @astrojs/telemetry 3.4.2 requires Node 22.23.4 or newer.
    assert _version(str(next(iter(_workflow_pins().values())))) >= (22, 23, 4)


def test_site_engine_floor_matches_astro_requirement() -> None:
    # site/.npmrc sets engine-strict, so the declared floor must cover the locked Astro packages.
    package = json.loads((ROOT / "site/package.json").read_text())
    lock = json.loads((ROOT / "site/package-lock.json").read_text())
    assert _version(package["engines"]["node"][2:]) >= (22, 23, 4)
    assert lock["packages"][""]["engines"] == package["engines"]
