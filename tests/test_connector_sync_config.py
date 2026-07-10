"""Tests for ``bin/connector-sync.py`` config loading."""

from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "connector-sync.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("connector_sync_under_test", BIN)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_stdlib_yaml_fallback_loads_documented_connectors_list(tmp_path, monkeypatch):
    config = tmp_path / "connectors.yaml"
    config.write_text(
        """
connectors:
  - name: linear
    type: linear
    enabled: true
    api_key_env: LINEAR_API_KEY
    default_repo: example-org/example-backend
    default_labels: [source:linear]
    filter:
      team_key: ENG
      state: Ready

  - name: sentry
    type: sentry
    enabled: false
    api_key_env: SENTRY_AUTH_TOKEN
    organization: example-org
    project: example-web
    min_severity: warning
    default_repo: example-org/example-web
""",
        encoding="utf-8",
    )
    real_import = builtins.__import__

    def import_without_yaml(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "yaml":
            raise ImportError("yaml is intentionally unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_yaml)

    module = _load_module()
    data = module._load_config(config)

    assert data == {
        "connectors": [
            {
                "name": "linear",
                "type": "linear",
                "enabled": True,
                "api_key_env": "LINEAR_API_KEY",
                "default_repo": "example-org/example-backend",
                "default_labels": ["source:linear"],
                "filter": {
                    "team_key": "ENG",
                    "state": "Ready",
                },
            },
            {
                "name": "sentry",
                "type": "sentry",
                "enabled": False,
                "api_key_env": "SENTRY_AUTH_TOKEN",
                "organization": "example-org",
                "project": "example-web",
                "min_severity": "warning",
                "default_repo": "example-org/example-web",
            },
        ]
    }
