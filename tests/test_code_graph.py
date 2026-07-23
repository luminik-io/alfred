from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from code_graph import (  # noqa: E402
    CODEGRAPH_SCHEMA,
    blast_radius_for_paths,
    export_codegraph,
    impact_brief_for_path,
    impact_for_path,
    render_blast_radius,
    render_impact_brief,
    summarize_codegraph,
)


def _sample_code_map() -> dict:
    return {
        "generated_at": "2026-06-30T20:00:00Z",
        "repos": {
            "web": {
                "head_sha": "abc123",
                "graph_summary": {
                    "files": 3,
                    "symbols": 4,
                    "imports": 2,
                    "languages": {"typescript": 3},
                    "truncated": False,
                },
                "files": [
                    {
                        "path": "src/App.tsx",
                        "language": "typescript",
                        "symbols": [{"name": "App", "line": 4}],
                        "imports": ["./Widget"],
                    },
                    {
                        "path": "src/Widget.tsx",
                        "language": "typescript",
                        "symbols": [{"name": "Widget", "line": 2}],
                        "imports": ["./api"],
                    },
                    {
                        "path": "src/api.ts",
                        "language": "typescript",
                        "symbols": [{"name": "loadThing", "line": 8}],
                        "imports": [],
                    },
                ],
                "edges": [
                    {"from": "src/App.tsx", "to": "./Widget", "kind": "import"},
                    {"from": "src/Widget.tsx", "to": "./api", "kind": "import"},
                ],
                "api_calls": [{"method": "GET", "path": "/api/v1/things", "file": "src/api.ts:9"}],
            }
        },
        "contract_drift": [
            {
                "caller": "web",
                "method": "GET",
                "path": "/api/v1/things",
                "normalized": "/v1/things",
                "file": "src/api.ts:9",
            }
        ],
    }


def test_export_codegraph_uses_stable_schema() -> None:
    exported = export_codegraph(_sample_code_map(), path=Path("/tmp/code-map.json"))

    assert exported["schema"] == CODEGRAPH_SCHEMA
    assert exported["source"] == {"kind": "alfred-code-map", "path": "/tmp/code-map.json"}
    assert exported["repos"][0]["name"] == "web"
    assert exported["repos"][0]["summary"]["files"] == 3
    assert exported["repos"][0]["contracts"]["api_calls"][0]["path"] == "/api/v1/things"


def test_export_codegraph_marks_in_memory_source() -> None:
    exported = export_codegraph(_sample_code_map())

    assert exported["source"] == {"kind": "in-memory-code-map", "path": None}


def test_summarize_codegraph_omits_raw_files() -> None:
    summary = summarize_codegraph(_sample_code_map(), repo="web")

    assert summary["repo_count"] == 1
    assert summary["repos"][0]["name"] == "web"
    assert summary["repos"][0]["api_call_count"] == 1
    assert "files" not in summary["repos"][0]


def test_summarize_codegraph_filters_drift_count_by_repo() -> None:
    code_map = _sample_code_map()
    code_map["repos"]["api"] = {"head_sha": "def456", "graph_summary": {"files": 1}}
    code_map["contract_drift"].append(
        {
            "caller": "api",
            "method": "GET",
            "path": "/v1/other",
            "normalized": "/v1/other",
            "file": "src/other.py:1",
        }
    )

    web_summary = summarize_codegraph(code_map, repo="web")
    all_summary = summarize_codegraph(code_map)

    assert web_summary["contract_drift_count"] == 1
    assert all_summary["contract_drift_count"] == 2


def test_summarize_codegraph_filters_drift_count_by_truncated_repos() -> None:
    code_map = _sample_code_map()
    code_map["repos"]["api"] = {"head_sha": "def456", "graph_summary": {"files": 1}}
    code_map["contract_drift"].append(
        {
            "caller": "api",
            "method": "GET",
            "path": "/v1/other",
            "normalized": "/v1/other",
            "file": "src/other.py:1",
        }
    )

    summary = summarize_codegraph(code_map, limit=1)

    assert [repo["name"] for repo in summary["repos"]] == ["api"]
    assert summary["contract_drift_count"] == 1


def test_impact_for_path_resolves_local_imports_and_contracts() -> None:
    impact = impact_for_path(_sample_code_map(), repo="web", path="src/api.ts")

    assert impact["matched_file"] == "src/api.ts"
    assert impact["match_status"] == "exact"
    assert impact["symbols"] == [{"name": "loadThing", "line": 8}]
    assert impact["imported_by"] == [
        {
            "from": "src/Widget.tsx",
            "to": "./api",
            "resolved_to": "src/api.ts",
            "kind": "import",
        }
    ]
    assert impact["contracts"]["api_calls"][0]["method"] == "GET"
    assert impact["contract_drift"][0]["normalized"] == "/v1/things"


def test_impact_for_path_resolves_parent_directory_imports() -> None:
    code_map = _sample_code_map()
    repo = code_map["repos"]["web"]
    repo["files"].append(
        {
            "path": "src/components/Card.tsx",
            "language": "typescript",
            "symbols": [{"name": "Card", "line": 3}],
            "imports": ["../api"],
        }
    )
    repo["edges"].append({"from": "src/components/Card.tsx", "to": "../api", "kind": "import"})

    impact = impact_for_path(code_map, repo="web", path="src/api.ts")

    assert {
        "from": "src/components/Card.tsx",
        "to": "../api",
        "resolved_to": "src/api.ts",
        "kind": "import",
    } in impact["imported_by"]


def test_impact_for_path_resolves_jsx_directory_imports() -> None:
    code_map = _sample_code_map()
    repo = code_map["repos"]["web"]
    repo["files"].extend(
        [
            {
                "path": "src/Page.jsx",
                "language": "javascript",
                "symbols": [{"name": "Page", "line": 1}],
                "imports": ["./components"],
            },
            {
                "path": "src/components/index.jsx",
                "language": "javascript",
                "symbols": [{"name": "Components", "line": 1}],
                "imports": [],
            },
        ]
    )
    repo["edges"].append({"from": "src/Page.jsx", "to": "./components", "kind": "import"})

    impact = impact_for_path(code_map, repo="web", path="src/components/index.jsx")

    assert impact["imported_by"] == [
        {
            "from": "src/Page.jsx",
            "to": "./components",
            "resolved_to": "src/components/index.jsx",
            "kind": "import",
        }
    ]


def test_impact_for_path_resolves_python_relative_module_imports() -> None:
    code_map = _sample_code_map()
    repo = code_map["repos"]["web"]
    repo["files"].extend(
        [
            {
                "path": "pkg/service.py",
                "language": "python",
                "symbols": [{"name": "Service", "line": 1}],
                "imports": [".utils"],
            },
            {
                "path": "pkg/utils.py",
                "language": "python",
                "symbols": [{"name": "parse", "line": 1}],
                "imports": [],
            },
        ]
    )
    repo["edges"].append({"from": "pkg/service.py", "to": ".utils", "kind": "import"})

    impact = impact_for_path(code_map, repo="web", path="pkg/utils.py")

    assert impact["imported_by"] == [
        {
            "from": "pkg/service.py",
            "to": ".utils",
            "resolved_to": "pkg/utils.py",
            "kind": "import",
        }
    ]


def test_impact_for_missing_path_does_not_match_unresolved_imports() -> None:
    impact = impact_for_path(_sample_code_map(), repo="web", path="src/Missing.ts")

    assert impact["matched_file"] is None
    assert impact["match_status"] == "not_found"
    assert impact["imported_by"] == []
    assert impact["imports_resolved"] == []


def test_impact_for_ambiguous_suffix_match_names_candidates() -> None:
    code_map = _sample_code_map()
    repo = code_map["repos"]["web"]
    repo["files"].append(
        {
            "path": "tests/api.ts",
            "language": "typescript",
            "symbols": [{"name": "testApi", "line": 1}],
            "imports": [],
        }
    )

    impact = impact_for_path(code_map, repo="web", path="api.ts")

    assert impact["matched_file"] is None
    assert impact["match_status"] == "ambiguous"
    assert impact["candidate_matches"] == ["src/api.ts", "tests/api.ts"]


def test_impact_brief_summarizes_blast_radius() -> None:
    brief = impact_brief_for_path(_sample_code_map(), repo="web", path="src/api.ts")

    assert brief["kind"] == "impact-brief"
    assert brief["level"] == "high"
    assert brief["counts"]["direct_dependents"] == 1
    assert brief["counts"]["contract_surfaces"] == 1
    assert brief["counts"]["contract_drift"] == 1
    assert brief["direct_dependents"] == [
        {"path": "src/Widget.tsx", "via": "./api", "kind": "import"}
    ]
    assert brief["contract_surfaces"] == [
        {
            "kind": "api_call",
            "method": "GET",
            "path": "/api/v1/things",
            "file": "src/api.ts:9",
        }
    ]
    assert "contract drift" in " ".join(brief["next_checks"]).lower()


def test_impact_brief_handles_unknown_path_without_guessing() -> None:
    brief = impact_brief_for_path(_sample_code_map(), repo="web", path="src/Missing.ts")

    assert brief["level"] == "unknown"
    assert brief["matched_file"] is None
    assert brief["match_status"] == "not_found"
    assert brief["direct_dependents"] == []
    assert brief["next_checks"] == [
        "Refresh the code map, then verify the path is tracked before relying on this brief."
    ]


def test_impact_brief_scores_before_display_limit() -> None:
    code_map = _sample_code_map()
    repo = code_map["repos"]["web"]
    repo["files"].extend(
        [
            {
                "path": "src/Page.tsx",
                "language": "typescript",
                "symbols": [{"name": "Page", "line": 1}],
                "imports": ["./Widget"],
            },
            {
                "path": "src/Dialog.tsx",
                "language": "typescript",
                "symbols": [{"name": "Dialog", "line": 1}],
                "imports": ["./Widget"],
            },
        ]
    )
    repo["edges"].extend(
        [
            {"from": "src/Page.tsx", "to": "./Widget", "kind": "import"},
            {"from": "src/Dialog.tsx", "to": "./Widget", "kind": "import"},
        ]
    )

    brief = impact_brief_for_path(code_map, repo="web", path="src/Widget.tsx", limit=1)

    assert brief["level"] == "medium"
    assert brief["counts"]["direct_dependents"] == 3
    assert len(brief["direct_dependents"]) == 1


def test_impact_brief_treats_unique_suffix_as_resolved_with_caveat() -> None:
    brief = impact_brief_for_path(_sample_code_map(), repo="web", path="Widget.tsx")

    assert brief["matched_file"] == "src/Widget.tsx"
    assert brief["match_status"] == "suffix"
    assert brief["level"] == "low"
    assert "path matched by suffix" in brief["reasons"]


def test_render_impact_brief_is_prompt_ready() -> None:
    text = render_impact_brief(
        impact_brief_for_path(_sample_code_map(), repo="web", path="src/api.ts")
    )

    assert "Blast radius: web:src/api.ts" in text
    assert "Level: high" in text
    assert "Direct dependents:" in text
    assert "- src/Widget.tsx imports ./api" in text
    assert "Next checks:" in text


def test_blast_radius_aggregates_changed_paths() -> None:
    blast = blast_radius_for_paths(
        _sample_code_map(),
        repo="web",
        paths=["src/api.ts", "src/Widget.tsx", "src/api.ts"],
    )

    assert blast["kind"] == "blast-radius"
    assert blast["level"] == "high"
    assert blast["counts"]["changed_paths"] == 2
    assert blast["counts"]["direct_dependents"] == 2
    assert blast["counts"]["contract_surfaces"] == 1
    assert blast["direct_dependents"] == [
        {
            "changed_path": "src/api.ts",
            "path": "src/Widget.tsx",
            "via": "./api",
            "kind": "import",
            "also_changed": True,
        },
        {
            "changed_path": "src/Widget.tsx",
            "path": "src/App.tsx",
            "via": "./Widget",
            "kind": "import",
            "also_changed": False,
        },
    ]


def test_blast_radius_counts_exact_and_suffix_alias_once() -> None:
    code_map = _sample_code_map()
    code_map["contract_drift"] = []

    blast = blast_radius_for_paths(
        code_map,
        repo="web",
        paths=["src/api.ts", "api.ts"],
    )

    assert blast["level"] == "medium"
    assert blast["counts"]["changed_paths"] == 2
    assert blast["counts"]["matched_paths"] == 1
    assert blast["counts"]["contract_surfaces"] == 1
    assert blast["counts"]["contract_drift"] == 0
    assert blast["contract_surfaces"] == [
        {
            "changed_path": "src/api.ts",
            "kind": "api_call",
            "method": "GET",
            "path": "/api/v1/things",
            "file": "src/api.ts:9",
        }
    ]
    assert "1 API/route surface(s)" in blast["reasons"]


def test_blast_radius_counts_shared_dependents_once() -> None:
    code_map = {
        "generated_at": "2026-06-30T20:00:00Z",
        "repos": {
            "web": {
                "head_sha": "abc123",
                "graph_summary": {"files": 3, "symbols": 3, "imports": 2},
                "files": [
                    {
                        "path": "src/A.ts",
                        "language": "typescript",
                        "symbols": [{"name": "A", "line": 1}],
                        "imports": [],
                    },
                    {
                        "path": "src/B.ts",
                        "language": "typescript",
                        "symbols": [{"name": "B", "line": 1}],
                        "imports": [],
                    },
                    {
                        "path": "src/App.tsx",
                        "language": "typescript",
                        "symbols": [{"name": "App", "line": 1}],
                        "imports": ["./A", "./B"],
                    },
                ],
                "edges": [
                    {"from": "src/App.tsx", "to": "./A", "kind": "import"},
                    {"from": "src/App.tsx", "to": "./B", "kind": "import"},
                ],
            }
        },
        "contract_drift": [],
    }

    blast = blast_radius_for_paths(code_map, repo="web", paths=["src/A.ts", "src/B.ts"])

    assert blast["counts"]["direct_dependents"] == 1
    assert "1 direct dependent file(s)" in blast["reasons"]


def test_blast_radius_tracks_unmapped_and_ambiguous_paths() -> None:
    code_map = _sample_code_map()
    code_map["repos"]["web"]["files"].append(
        {
            "path": "tests/api.ts",
            "language": "typescript",
            "symbols": [{"name": "testApi", "line": 1}],
            "imports": [],
        }
    )

    blast = blast_radius_for_paths(
        code_map,
        repo="web",
        paths=["api.ts", "src/Missing.ts"],
    )

    assert blast["level"] == "high"
    assert blast["counts"]["ambiguous_paths"] == 1
    assert blast["counts"]["unmapped_paths"] == 1
    assert blast["changed_paths"][0]["candidate_matches"] == ["src/api.ts", "tests/api.ts"]


def test_blast_radius_scores_before_display_limit() -> None:
    code_map = _sample_code_map()
    repo = code_map["repos"]["web"]
    repo["files"].extend(
        [
            {
                "path": "src/Page.tsx",
                "language": "typescript",
                "symbols": [{"name": "Page", "line": 1}],
                "imports": ["./Widget"],
            },
            {
                "path": "src/Dialog.tsx",
                "language": "typescript",
                "symbols": [{"name": "Dialog", "line": 1}],
                "imports": ["./Widget"],
            },
        ]
    )
    repo["edges"].extend(
        [
            {"from": "src/Page.tsx", "to": "./Widget", "kind": "import"},
            {"from": "src/Dialog.tsx", "to": "./Widget", "kind": "import"},
        ]
    )

    blast = blast_radius_for_paths(code_map, repo="web", paths=["src/Widget.tsx"], limit=1)

    assert blast["level"] == "medium"
    assert blast["counts"]["direct_dependents"] == 3
    assert len(blast["direct_dependents"]) == 1


def test_impact_brief_filters_dependencies_before_display_limit() -> None:
    code_map = _sample_code_map()
    repo = code_map["repos"]["web"]
    repo["files"] = [
        {
            "path": "src/Widget.tsx",
            "language": "typescript",
            "symbols": [{"name": "Widget", "line": 1}],
            "imports": ["react", "./Local"],
        },
        {
            "path": "src/Local.ts",
            "language": "typescript",
            "symbols": [{"name": "Local", "line": 1}],
            "imports": [],
        },
    ]
    repo["edges"] = [
        {"from": "src/Widget.tsx", "to": "react", "kind": "import"},
        {"from": "src/Widget.tsx", "to": "./Local", "kind": "import"},
    ]

    brief = impact_brief_for_path(code_map, repo="web", path="src/Widget.tsx", limit=1)

    assert brief["counts"]["direct_dependencies"] == 1
    assert brief["direct_dependencies"] == [
        {"path": "src/Local.ts", "via": "./Local", "kind": "import"}
    ]


def test_blast_radius_counts_dependents_beyond_display_cap() -> None:
    code_map = _sample_code_map()
    repo = code_map["repos"]["web"]
    widget = next(file_info for file_info in repo["files"] if file_info["path"] == "src/Widget.tsx")
    repo["files"] = [widget] + [
        {
            "path": f"src/Consumer{index}.tsx",
            "language": "typescript",
            "symbols": [{"name": f"Consumer{index}", "line": 1}],
            "imports": ["./Widget"],
        }
        for index in range(250)
    ]
    repo["edges"] = [
        {"from": f"src/Consumer{index}.tsx", "to": "./Widget", "kind": "import"}
        for index in range(250)
    ]

    brief = impact_brief_for_path(code_map, repo="web", path="src/Widget.tsx", limit=5)
    blast = blast_radius_for_paths(code_map, repo="web", paths=["src/Widget.tsx"], limit=5)

    assert brief["counts"]["direct_dependents"] == 250
    assert len(brief["direct_dependents"]) == 5
    assert blast["counts"]["direct_dependents"] == 250
    assert len(blast["direct_dependents"]) == 5
    assert "250 direct dependent file(s)" in blast["reasons"]


def test_blast_radius_honors_requested_limit_above_brief_default_cap() -> None:
    code_map = _sample_code_map()
    repo = code_map["repos"]["web"]
    widget = next(file_info for file_info in repo["files"] if file_info["path"] == "src/Widget.tsx")
    repo["files"] = [widget] + [
        {
            "path": f"src/Consumer{index}.tsx",
            "language": "typescript",
            "symbols": [{"name": f"Consumer{index}", "line": 1}],
            "imports": ["./Widget"],
        }
        for index in range(150)
    ]
    repo["edges"] = [
        {"from": f"src/Consumer{index}.tsx", "to": "./Widget", "kind": "import"}
        for index in range(150)
    ]

    blast = blast_radius_for_paths(code_map, repo="web", paths=["src/Widget.tsx"], limit=150)

    assert blast["counts"]["direct_dependents"] == 150
    assert len(blast["direct_dependents"]) == 150


def test_render_blast_radius_is_prompt_ready() -> None:
    text = render_blast_radius(
        blast_radius_for_paths(
            _sample_code_map(),
            repo="web",
            paths=["src/api.ts", "src/Widget.tsx"],
        )
    )

    assert "Blast radius: web" in text
    assert "Level: high" in text
    assert "Changed paths:" in text
    assert "- src/Widget.tsx depends on src/api.ts via ./api" in text
    assert "Contract drift:" in text
    assert "- src/api.ts GET /api/v1/things src/api.ts:9" in text
    assert "Next checks:" in text


def test_code_map_cli_exports_contract(tmp_path: Path) -> None:
    code_map = tmp_path / "code-map.json"
    code_map.write_text(json.dumps(_sample_code_map()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "alfred"),
            "code-map",
            "export",
            "--map",
            str(code_map),
            "--summary-only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == CODEGRAPH_SCHEMA
    assert payload["repos"][0]["name"] == "web"
    assert "files" not in payload["repos"][0]


def test_code_map_cli_builds_local_repo_snapshot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (src / "Widget.tsx").write_text(
        "import { Local } from './Local';\nexport function Widget() { return Local(); }\n",
        encoding="utf-8",
    )
    (src / "Local.ts").write_text(
        "export function Local() { return 'ok'; }\n",
        encoding="utf-8",
    )
    output = tmp_path / "code-map.json"
    alfred_home = tmp_path / "alfred-home"
    pause_dir = alfred_home / "state" / "_paused"
    pause_dir.mkdir(parents=True)
    (pause_dir / "code-map-refresh").write_text("scheduled refresh paused", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "alfred"),
            "code-map",
            "build",
            ".",
            "--output",
            str(output),
            "--json",
        ],
        cwd=repo,
        env={**os.environ, "ALFRED_HOME": str(alfred_home)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert payload == persisted
    assert "." in payload["repos"]
    assert {file_info["path"] for file_info in payload["repos"]["."]["files"]} == {
        "src/Local.ts",
        "src/Widget.tsx",
    }
    assert payload["repos"]["."]["edges"] == [
        {"from": "src/Widget.tsx", "kind": "import", "to": "./Local"}
    ]


def test_code_map_cli_renders_impact_brief(tmp_path: Path) -> None:
    code_map = tmp_path / "code-map.json"
    code_map.write_text(json.dumps(_sample_code_map()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "alfred"),
            "code-map",
            "impact",
            "web",
            "src/api.ts",
            "--map",
            str(code_map),
            "--brief",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Blast radius: web:src/api.ts" in result.stdout
    assert "Level: high" in result.stdout


def test_code_map_cli_renders_blast_radius_json(tmp_path: Path) -> None:
    code_map = tmp_path / "code-map.json"
    code_map.write_text(json.dumps(_sample_code_map()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "alfred"),
            "code-map",
            "blast-radius",
            "web",
            "src/api.ts",
            "src/Widget.tsx",
            "--map",
            str(code_map),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["kind"] == "blast-radius"
    assert payload["level"] == "high"
    assert payload["counts"]["changed_paths"] == 2


def test_code_map_cli_rejects_too_many_blast_radius_paths(tmp_path: Path) -> None:
    code_map = tmp_path / "code-map.json"
    code_map.write_text(json.dumps(_sample_code_map()), encoding="utf-8")
    paths = [f"src/File{index}.ts" for index in range(201)]

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "alfred"),
            "code-map",
            "blast-radius",
            "web",
            *paths,
            "--map",
            str(code_map),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "blast-radius supports at most 200 paths" in result.stderr
