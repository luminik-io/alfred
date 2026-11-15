"""Workflow Node pins stay in step and satisfy the packages' engine floors."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = [ROOT / "site/package.json", ROOT / "clients/desktop/package.json"]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _workflow_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for match in re.finditer(r'node-version:\s*"([^"]+)"', workflow.read_text()):
            pins[f"{workflow.name}:{match.start()}"] = match.group(1)
    return pins


def test_every_workflow_pins_the_same_node_version() -> None:
    pins = _workflow_pins()
    assert pins
    assert len(set(pins.values())) == 1, pins


def test_node_pin_meets_each_package_engine_floor() -> None:
    pinned = _version(next(iter(_workflow_pins().values())))
    for package in PACKAGES:
        floor = json.loads(package.read_text())["engines"]["node"]
        assert floor.startswith(">="), package
        assert pinned >= _version(floor[2:]), package


def test_node_pin_is_new_enough_for_current_astro_packages() -> None:
    # @astrojs/telemetry 3.4.2 requires Node 22.23.4 or newer.
    assert _version(next(iter(_workflow_pins().values()))) >= (22, 23, 4)
