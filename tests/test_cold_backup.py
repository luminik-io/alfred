"""Regression coverage for the weekly cold-backup runner."""

from __future__ import annotations

import importlib.util
import sys
import tarfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BIN = _ROOT / "bin"
_LIB = _ROOT / "lib"


def _load_backup(monkeypatch, tmp_path):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    if str(_LIB) not in sys.path:
        sys.path.insert(0, str(_LIB))
    for stale in list(sys.modules):
        if stale.startswith("agent_runner") or stale == "cold_backup_under_test":
            sys.modules.pop(stale, None)
    spec = importlib.util.spec_from_file_location(
        "cold_backup_under_test", _BIN / "alfred-cold-backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cold_backup_under_test"] = module
    spec.loader.exec_module(module)
    return module


def test_create_archive_includes_state_cron_and_launch_agents(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)
    runtime_home = tmp_path / "alfred"
    state = runtime_home / "state" / "lucius"
    state.mkdir(parents=True)
    (state / "spend-2026-05-25.json").write_text("{}")
    cron = runtime_home / "cron"
    cron.mkdir()
    (cron / "jobs.json").write_text("[]")
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "alfred.lucius.plist").write_text("<plist />")
    (launch_agents / "com.example.other.plist").write_text("<plist />")

    archive, included = backup.create_archive(
        output_dir=tmp_path / "out",
        stamp="2026-05-25",
        runtime_home=runtime_home,
        home=home,
    )

    assert archive.name == "2026-05-25.tar.gz"
    assert included == [
        "alfred/state",
        "alfred/cron/jobs.json",
        "LaunchAgents/alfred.lucius.plist",
    ]
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert "alfred/state/lucius/spend-2026-05-25.json" in names
    assert "alfred/cron/jobs.json" in names
    assert "LaunchAgents/alfred.lucius.plist" in names
    assert "LaunchAgents/com.example.other.plist" not in names


def test_prune_remote_backups_keeps_latest(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)
    calls: list[list[str]] = []

    def fake_aws(args, *, timeout=120):
        calls.append(args)
        if args[:2] == ["s3", "ls"]:
            stdout = "\n".join(
                [
                    "2026-05-01 02:00:00       1 2026-05-01.tar.gz",
                    "2026-05-08 02:00:00       1 2026-05-08.tar.gz",
                    "2026-05-15 02:00:00       1 2026-05-15.tar.gz",
                    "2026-05-22 02:00:00       1 2026-05-22.tar.gz",
                ]
            )
            return backup.subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        return backup.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(backup, "_aws", fake_aws)

    removed = backup.prune_remote_backups("s3://bucket/prefix", keep=2)

    assert removed == [
        "s3://bucket/prefix/2026-05-01.tar.gz",
        "s3://bucket/prefix/2026-05-08.tar.gz",
    ]
    assert calls[-2:] == [
        ["s3", "rm", "s3://bucket/prefix/2026-05-01.tar.gz"],
        ["s3", "rm", "s3://bucket/prefix/2026-05-08.tar.gz"],
    ]


def test_upload_archive_access_denied_includes_policy_hint(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)
    archive = tmp_path / "2026-06-01.tar.gz"
    archive.write_text("backup")

    def fake_aws(args, *, timeout=120):
        assert args[:2] == ["s3", "cp"]
        return backup.subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr=(
                "An error occurred (AccessDenied) when calling the "
                "CreateMultipartUpload operation: not authorized"
            ),
        )

    monkeypatch.setattr(backup, "_aws", fake_aws)

    try:
        backup.upload_archive(archive, "s3://bucket/alfred-backups")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("upload_archive should fail")

    assert "s3:PutObject" in message
    assert "s3:AbortMultipartUpload" in message
    assert "arn:aws:s3:::bucket/alfred-backups/2026-06-01.tar.gz" in message


def test_remote_list_access_denied_includes_policy_hint(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)

    def fake_aws(args, *, timeout=120):
        assert args[:2] == ["s3", "ls"]
        return backup.subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="AccessDenied: not authorized",
        )

    monkeypatch.setattr(backup, "_aws", fake_aws)

    try:
        backup.prune_remote_backups("s3://bucket/alfred-backups", keep=2)
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("prune_remote_backups should fail")

    assert "s3:ListBucket" in message
    assert "arn:aws:s3:::bucket" in message
    assert "s3:prefix=alfred-backups/*" in message


def test_remote_permission_smoke_writes_lists_and_deletes(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    timeouts: list[int] = []

    def fake_aws(args, *, timeout=120):
        calls.append(args)
        timeouts.append(timeout)
        return backup.subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    monkeypatch.setattr(backup, "_aws", fake_aws)
    monkeypatch.setattr(backup.os, "getpid", lambda: 1234)
    monotonic_values = iter([100.0, 100.0, 105.0, 121.0])
    monkeypatch.setattr(backup.time, "monotonic", lambda: next(monotonic_values))

    uri = backup.remote_permission_smoke("s3://bucket/alfred-backups")

    assert uri == "s3://bucket/alfred-backups/.doctor-smoke-1234"
    assert calls[0][:7] == [
        "s3api",
        "put-object",
        "--bucket",
        "bucket",
        "--key",
        "alfred-backups/.doctor-smoke-1234",
        "--body",
    ]
    assert calls[0][7].startswith("/var/") or calls[0][7].startswith("/tmp/")
    assert calls[1:] == [
        [
            "s3api",
            "list-objects-v2",
            "--bucket",
            "bucket",
            "--prefix",
            "alfred-backups/",
            "--max-items",
            "1",
        ],
        [
            "s3api",
            "delete-object",
            "--bucket",
            "bucket",
            "--key",
            "alfred-backups/.doctor-smoke-1234",
        ],
    ]
    assert timeouts == [20, 15, 3]


def test_remote_permission_smoke_can_skip_retention_permissions(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)
    calls: list[list[str]] = []

    def fake_aws(args, *, timeout=120):
        calls.append(args)
        return backup.subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    monkeypatch.setattr(backup, "_aws", fake_aws)
    monkeypatch.setattr(backup.os, "getpid", lambda: 1234)

    uri = backup.remote_permission_smoke("s3://bucket/alfred-backups", require_retention=False)

    assert uri == "s3://bucket/alfred-backups/.doctor-smoke-1234"
    assert len(calls) == 1
    assert calls[0][:7] == [
        "s3api",
        "put-object",
        "--bucket",
        "bucket",
        "--key",
        "alfred-backups/.doctor-smoke-1234",
        "--body",
    ]


def test_remote_permission_smoke_upload_access_denied_includes_hint(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)

    def fake_aws(args, *, timeout=120):
        assert args[:2] == ["s3api", "put-object"]
        return backup.subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="AccessDenied: not authorized",
        )

    monkeypatch.setattr(backup, "_aws", fake_aws)
    monkeypatch.setattr(backup.os, "getpid", lambda: 1234)

    try:
        backup.remote_permission_smoke("s3://bucket/alfred-backups")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("remote_permission_smoke should fail")

    assert "permission smoke upload failed" in message
    assert "s3:PutObject" in message
    assert "arn:aws:s3:::bucket/alfred-backups/.doctor-smoke-1234" in message


def test_remote_permission_smoke_delete_failure_includes_hint(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)

    def fake_aws(args, *, timeout=120):
        if args[:2] == ["s3api", "delete-object"]:
            return backup.subprocess.CompletedProcess(
                args,
                1,
                stdout="",
                stderr="AccessDenied: not authorized",
            )
        return backup.subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    monkeypatch.setattr(backup, "_aws", fake_aws)
    monkeypatch.setattr(backup.os, "getpid", lambda: 1234)

    try:
        backup.remote_permission_smoke("s3://bucket/alfred-backups")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("remote_permission_smoke should fail")

    assert "permission smoke delete failed" in message
    assert "s3:DeleteObject" in message
    assert "arn:aws:s3:::bucket/alfred-backups/.doctor-smoke-1234" in message


def test_no_prune_can_be_set_by_env(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)
    monkeypatch.setenv("ALFRED_BACKUP_PRUNE", "0")

    args = backup._parser().parse_args([])

    assert args.no_prune is True


def test_local_only_can_be_set_by_env(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)
    monkeypatch.setenv("ALFRED_BACKUP_LOCAL_ONLY", "1")

    args = backup._parser().parse_args([])

    assert args.local_only is True


def test_confirm_uses_operator_command(monkeypatch, tmp_path):
    backup = _load_backup(monkeypatch, tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setenv("ALFRED_BACKUP_CONFIRM_CMD", "notify-cmd --stdin")
    monkeypatch.setattr(backup, "slack_post", lambda message: False)

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["input"] = kwargs["input"]
        return backup.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    assert backup.confirm("backup ok")
    assert captured == {"args": ["notify-cmd", "--stdin"], "input": "backup ok"}
