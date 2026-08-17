from __future__ import annotations

import json
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
BIN = REPO_ROOT / "bin" / "alfred"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from run_import import (  # noqa: E402
    ImportConflictError,
    ImportScopeError,
    import_run_transcript,
    remove_run_import,
)
from server.run_evidence import discover_imported_transcript_artifacts  # noqa: E402


def _load_cli():
    loader = SourceFileLoader("alfred_cli_run_import", str(BIN))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def _write_run(
    state_root: Path,
    *,
    agent: str = "reviewer",
    run_id: str = "run-7",
    repo: str = "example/app",
) -> Path:
    path = state_root / agent / "events" / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "schema_version": 1,
                        "agent": agent,
                        "firing_id": run_id,
                        "event": "issue_picked",
                        "repo": repo,
                        "number": 42,
                    }
                ),
                json.dumps(
                    {
                        "schema_version": 1,
                        "agent": agent,
                        "firing_id": run_id,
                        "event": "firing_complete",
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("engine", "managed_name"),
    (("claude", "transcript.jsonl"), ("codex", "stdout.txt"), ("opencode", "events.jsonl")),
)
def test_import_copies_repo_scoped_transcript_into_managed_state(
    tmp_path: Path,
    engine: str,
    managed_name: str,
) -> None:
    state_root = tmp_path / "state"
    _write_run(state_root)
    source = tmp_path / "operator" / "session.jsonl"
    source.parent.mkdir()
    source.write_text("private engine transcript\n", encoding="utf-8")

    result = import_run_transcript(
        state_root=state_root,
        agent="reviewer",
        run_id="run-7",
        repo="example/app",
        engine=engine,
        source=source,
    )

    managed = state_root / "imports" / "reviewer" / "run-7" / managed_name
    assert result.status == "imported"
    assert result.transcript_path == managed
    assert managed.read_text(encoding="utf-8") == "private engine transcript\n"
    assert source.read_text(encoding="utf-8") == "private engine transcript\n"
    manifest = json.loads((managed.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {
        "schema_version": 1,
        "run_id": "run-7",
        "agent": "reviewer",
        "repo": "example/app",
        "engine": engine,
        "transcript_name": managed_name,
        "sha256": result.sha256,
        "size_bytes": len("private engine transcript\n"),
        "imported_at": result.imported_at,
    }
    assert str(tmp_path) not in json.dumps(manifest)
    assert discover_imported_transcript_artifacts(
        state_root,
        agent="reviewer",
        run_id="run-7",
    ) == [managed]


def test_import_accepts_repo_url_fact_for_exact_slug(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    _write_run(state_root, repo="https://github.com/example/app")
    source = tmp_path / "session.txt"
    source.write_text("transcript\n", encoding="utf-8")

    result = import_run_transcript(
        state_root=state_root,
        agent="reviewer",
        run_id="run-7",
        repo="example/app",
        engine="codex",
        source=source,
    )

    assert result.status == "imported"


def test_import_is_idempotent_only_for_the_same_content_and_scope(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    _write_run(state_root)
    source = tmp_path / "session.txt"
    source.write_text("same transcript\n", encoding="utf-8")
    first = import_run_transcript(
        state_root=state_root,
        agent="reviewer",
        run_id="run-7",
        repo="example/app",
        engine="codex",
        source=source,
    )

    second = import_run_transcript(
        state_root=state_root,
        agent="reviewer",
        run_id="run-7",
        repo="example/app",
        engine="codex",
        source=source,
    )

    assert first.status == "imported"
    assert second.status == "unchanged"
    source.write_text("different transcript\n", encoding="utf-8")
    with pytest.raises(ImportConflictError, match="remove the existing import"):
        import_run_transcript(
            state_root=state_root,
            agent="reviewer",
            run_id="run-7",
            repo="example/app",
            engine="codex",
            source=source,
        )


@pytest.mark.parametrize(
    ("agent", "run_id", "repo"),
    (
        ("../reviewer", "run-7", "example/app"),
        ("reviewer", "../run-7", "example/app"),
        ("reviewer", "run-7", "example/app/extra"),
        ("reviewer", "run-7", "example/../app"),
    ),
)
def test_import_rejects_untrusted_identifiers(
    tmp_path: Path,
    agent: str,
    run_id: str,
    repo: str,
) -> None:
    source = tmp_path / "session.txt"
    source.write_text("transcript\n", encoding="utf-8")
    with pytest.raises(ValueError):
        import_run_transcript(
            state_root=tmp_path / "state",
            agent=agent,
            run_id=run_id,
            repo=repo,
            engine="codex",
            source=source,
        )


def test_import_fails_closed_when_run_repo_does_not_match(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    _write_run(state_root, repo="other/project")
    source = tmp_path / "session.txt"
    source.write_text("transcript\n", encoding="utf-8")

    with pytest.raises(ImportScopeError, match="does not record repository example/app"):
        import_run_transcript(
            state_root=state_root,
            agent="reviewer",
            run_id="run-7",
            repo="example/app",
            engine="codex",
            source=source,
        )

    assert not (state_root / "imports").exists()


def test_import_fails_closed_for_missing_run_or_source(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    source = tmp_path / "session.txt"
    source.write_text("transcript\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="run events"):
        import_run_transcript(
            state_root=state_root,
            agent="reviewer",
            run_id="run-7",
            repo="example/app",
            engine="codex",
            source=source,
        )

    _write_run(state_root)
    source.unlink()
    with pytest.raises(FileNotFoundError, match="transcript source"):
        import_run_transcript(
            state_root=state_root,
            agent="reviewer",
            run_id="run-7",
            repo="example/app",
            engine="codex",
            source=source,
        )


def test_remove_deletes_only_managed_copy_and_keeps_source(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    _write_run(state_root)
    source = tmp_path / "session.txt"
    source.write_text("transcript\n", encoding="utf-8")
    imported = import_run_transcript(
        state_root=state_root,
        agent="reviewer",
        run_id="run-7",
        repo="example/app",
        engine="codex",
        source=source,
    )

    result = remove_run_import(
        state_root=state_root,
        agent="reviewer",
        run_id="run-7",
        repo="example/app",
    )

    assert result.status == "removed"
    assert source.read_text(encoding="utf-8") == "transcript\n"
    assert not imported.transcript_path.exists()
    assert not imported.transcript_path.parent.exists()
    assert (
        remove_run_import(
            state_root=state_root,
            agent="reviewer",
            run_id="run-7",
            repo="example/app",
        ).status
        == "not_found"
    )


def test_remove_refuses_repo_mismatch_or_unknown_managed_files(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    _write_run(state_root)
    source = tmp_path / "session.txt"
    source.write_text("transcript\n", encoding="utf-8")
    imported = import_run_transcript(
        state_root=state_root,
        agent="reviewer",
        run_id="run-7",
        repo="example/app",
        engine="codex",
        source=source,
    )

    with pytest.raises(ImportScopeError, match="belongs to example/app"):
        remove_run_import(
            state_root=state_root,
            agent="reviewer",
            run_id="run-7",
            repo="other/project",
        )

    (imported.transcript_path.parent / "keep.txt").write_text("operator file\n", encoding="utf-8")
    with pytest.raises(ImportConflictError, match="unknown files"):
        remove_run_import(
            state_root=state_root,
            agent="reviewer",
            run_id="run-7",
            repo="example/app",
        )
    assert imported.transcript_path.exists()


def test_cli_import_and_remove_emit_clean_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    _write_run(state_root)
    source = tmp_path / "session.txt"
    source.write_text("transcript\n", encoding="utf-8")
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path))
    cli = _load_cli()

    assert (
        cli.main(
            [
                "evidence",
                "import",
                "--agent",
                "reviewer",
                "--run-id",
                "run-7",
                "--repo",
                "example/app",
                "--engine",
                "codex",
                "--source",
                str(source),
                "--json",
            ]
        )
        == 0
    )
    imported = json.loads(capsys.readouterr().out)
    assert imported["status"] == "imported"
    assert imported["agent"] == "reviewer"
    assert imported["run_id"] == "run-7"
    assert imported["repo"] == "example/app"
    assert imported["engine"] == "codex"
    assert imported["transcript_path"].endswith("/state/imports/reviewer/run-7/stdout.txt")

    assert (
        cli.main(
            [
                "evidence",
                "remove",
                "--agent",
                "reviewer",
                "--run-id",
                "run-7",
                "--repo",
                "example/app",
                "--json",
            ]
        )
        == 0
    )
    removed = json.loads(capsys.readouterr().out)
    assert removed["status"] == "removed"
    assert source.exists()


def test_cli_import_reports_scope_error_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    _write_run(state_root, repo="other/project")
    source = tmp_path / "session.txt"
    source.write_text("transcript\n", encoding="utf-8")
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path))
    cli = _load_cli()

    assert (
        cli.main(
            [
                "evidence",
                "import",
                "--agent",
                "reviewer",
                "--run-id",
                "run-7",
                "--repo",
                "example/app",
                "--engine",
                "codex",
                "--source",
                str(source),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does not record repository example/app" in captured.err
    assert "Traceback" not in captured.err
