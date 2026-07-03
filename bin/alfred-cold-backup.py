#!/usr/bin/env python3
"""Scheduled cold backup for Alfred runtime state (opt-in).

Archives ``$ALFRED_HOME/state``, the cron job store, and the runtime's own
launchd plists, then optionally uploads the tarball to an S3 prefix and
prunes old backups to a retention window. With no destination configured,
use ``--local-only`` to just write the archive; the S3 path stays inert.

Configuration:

- ``ALFRED_BACKUP_DEST`` - ``s3://bucket/prefix`` upload target (required
  unless ``--local-only``).
- ``ALFRED_BACKUP_AWS_PROFILE`` / ``ALFRED_AWS_PROFILE`` - AWS profile for
  the upload (defaults to the neutral ``acme-host`` placeholder; set your
  own).
- ``ALFRED_LAUNCHD_LABEL_PREFIX`` - reverse-DNS label prefix for the plists
  to include (defaults to ``alfred``).
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (
    _HERE.parent / "lib",
    Path(os.environ.get("ALFRED_HOME", "")) / "lib",
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from agent_runner import (  # noqa: E402
    ALFRED_HOME,
    HOME,
    PreflightFailed,
    PreflightSpec,
    doctor_mode,
    preflight,
    slack_post,
    with_lock,
)
from agent_runner.paths import _aws_secret_env  # noqa: E402

AGENT = "cold-backup"
# No default S3 destination: point this at your own bucket via ``--dest`` or
# ``ALFRED_BACKUP_DEST``. ``--local-only`` skips S3 entirely.
DEFAULT_DEST = os.environ.get("ALFRED_BACKUP_DEST", "")
# Reverse-DNS label prefix for the runtime's launchd jobs. Backups include the
# operator's own agent plists; override if you renamed the prefix.
PLIST_LABEL_PREFIX = os.environ.get("ALFRED_LAUNCHD_LABEL_PREFIX", "alfred")
DEFAULT_KEEP = 8
SMOKE_AWS_BUDGET_SECONDS = 20
SMOKE_AWS_MIN_TIMEOUT_SECONDS = 3
FALSE_VALUES = {"0", "false", "no", "off"}
TRUE_VALUES = {"1", "true", "yes", "on"}


def _backup_profile() -> str:
    return (
        os.environ.get("ALFRED_BACKUP_AWS_PROFILE")
        or os.environ.get("ALFRED_AWS_PROFILE")
        or "acme-host"
    )


PREFLIGHT = PreflightSpec(
    agent=AGENT,
    bins=["aws"],
    aws_profile=_backup_profile(),
    require_gh_auth=False,
)


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


def _add_if_exists(tar: tarfile.TarFile, path: Path, arcname: str) -> bool:
    if not path.exists():
        return False
    tar.add(path, arcname=arcname, recursive=True)
    return True


def create_archive(
    *,
    output_dir: Path,
    stamp: str,
    runtime_home: Path = ALFRED_HOME,
    home: Path = HOME,
) -> tuple[Path, list[str]]:
    """Create a gzipped archive and return (path, included arc names)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{stamp}.tar.gz"
    included: list[str] = []
    launch_agents = home / "Library" / "LaunchAgents"

    with tarfile.open(archive, "w:gz") as tar:
        entries = [
            (runtime_home / "state", "alfred/state"),
            (runtime_home / "cron" / "jobs.json", "alfred/cron/jobs.json"),
        ]
        for path, arcname in entries:
            if _add_if_exists(tar, path, arcname):
                included.append(arcname)
        if launch_agents.is_dir():
            for plist in sorted(launch_agents.glob(f"{PLIST_LABEL_PREFIX}.*.plist")):
                arcname = f"LaunchAgents/{plist.name}"
                if _add_if_exists(tar, plist, arcname):
                    included.append(arcname)

    archive.chmod(0o600)
    return archive, included


def _aws(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["aws", *args],
        env=_aws_secret_env("ALFRED_BACKUP_AWS_PROFILE"),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _remaining_smoke_timeout(deadline: float) -> int:
    return max(SMOKE_AWS_MIN_TIMEOUT_SECONDS, int(deadline - time.monotonic()))


def _join_s3(prefix: str, name: str) -> str:
    return f"{prefix.rstrip('/')}/{name}"


def _split_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        return "", ""
    rest = uri[5:]
    bucket, _, key = rest.partition("/")
    return bucket, key


def _permission_hint(detail: str, *, uri: str, operation: str) -> str:
    if "AccessDenied" not in detail and "not authorized" not in detail:
        return _short_detail(detail)

    profile = _backup_profile()
    bucket, key = _split_s3_uri(uri)
    if operation == "upload":
        resource = f"arn:aws:s3:::{bucket}/{key}" if bucket and key else uri
        hint = f"profile {profile!r} needs s3:PutObject and s3:AbortMultipartUpload on {resource}"
    elif operation == "list":
        prefix = key.rstrip("/")
        scope = f" with s3:prefix={prefix}/*" if prefix else ""
        resource = f"arn:aws:s3:::{bucket}" if bucket else uri
        hint = f"profile {profile!r} needs s3:ListBucket on {resource}{scope}"
    else:
        resource = f"arn:aws:s3:::{bucket}/{key}" if bucket and key else uri
        hint = f"profile {profile!r} needs s3:DeleteObject on {resource}"
    return f"{_short_detail(detail)}\nAction: {hint}."


def _short_detail(detail: str, limit: int = 500) -> str:
    text = " ".join(str(detail or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def upload_archive(archive: Path, dest: str) -> str:
    target = _join_s3(dest, archive.name)
    res = _aws(["s3", "cp", str(archive), target], timeout=300)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "no output").strip()
        raise RuntimeError(
            "upload failed: " + _permission_hint(detail, uri=target, operation="upload")
        )
    return target


def _remote_backup_names(dest: str) -> list[str]:
    res = _aws(["s3", "ls", dest.rstrip("/") + "/"])
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "no output").strip()
        raise RuntimeError("list failed: " + _permission_hint(detail, uri=dest, operation="list"))
    names: list[str] = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        name = parts[-1]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.tar\.gz", name):
            names.append(name)
    return sorted(set(names))


def prune_remote_backups(dest: str, *, keep: int) -> list[str]:
    if keep <= 0:
        return []
    names = _remote_backup_names(dest)
    victims = names[:-keep]
    removed: list[str] = []
    for name in victims:
        uri = _join_s3(dest, name)
        res = _aws(["s3", "rm", uri])
        if res.returncode != 0:
            detail = (res.stderr or res.stdout or "no output").strip()
            raise RuntimeError(
                f"delete failed for {name}: "
                + _permission_hint(detail, uri=uri, operation="delete")
            )
        removed.append(uri)
    return removed


def remote_permission_smoke(dest: str, *, require_retention: bool = True) -> str:
    """Prove the backup profile can write, and optionally list/delete, the prefix."""
    marker = f".doctor-smoke-{os.getpid()}"
    uri = _join_s3(dest, marker)
    bucket, key = _split_s3_uri(uri)
    if not bucket or not key:
        raise RuntimeError(f"backup destination must be an s3:// URI, got {dest!r}")

    deadline = time.monotonic() + SMOKE_AWS_BUDGET_SECONDS

    with tempfile.NamedTemporaryFile(prefix="alfred-cold-backup-smoke-") as body:
        put = _aws(
            ["s3api", "put-object", "--bucket", bucket, "--key", key, "--body", body.name],
            timeout=_remaining_smoke_timeout(deadline),
        )
    if put.returncode != 0:
        detail = (put.stderr or put.stdout or "no output").strip()
        raise RuntimeError(
            "permission smoke upload failed: "
            + _permission_hint(detail, uri=uri, operation="upload")
        )
    if not require_retention:
        return uri

    try:
        prefix = key.rsplit("/", 1)[0] + "/" if "/" in key else ""
        listed = _aws(
            [
                "s3api",
                "list-objects-v2",
                "--bucket",
                bucket,
                "--prefix",
                prefix,
                "--max-items",
                "1",
            ],
            timeout=_remaining_smoke_timeout(deadline),
        )
        if listed.returncode != 0:
            detail = (listed.stderr or listed.stdout or "no output").strip()
            raise RuntimeError(
                "permission smoke list failed: "
                + _permission_hint(detail, uri=f"s3://{bucket}/{prefix}", operation="list")
            )
    finally:
        deleted = _aws(
            ["s3api", "delete-object", "--bucket", bucket, "--key", key],
            timeout=_remaining_smoke_timeout(deadline),
        )
        if deleted.returncode != 0:
            detail = (deleted.stderr or deleted.stdout or "no output").strip()
            raise RuntimeError(
                "permission smoke delete failed: "
                + _permission_hint(detail, uri=uri, operation="delete")
            )

    return uri


def confirm(message: str) -> bool:
    """Send confirmation through an operator command or Slack fallback."""
    cmd = os.environ.get("ALFRED_BACKUP_CONFIRM_CMD", "").strip()
    if cmd:
        try:
            res = subprocess.run(
                shlex.split(cmd),
                input=message,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return res.returncode == 0
    return slack_post(message)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and upload an Alfred cold backup.")
    parser.add_argument("--dest", default=os.environ.get("ALFRED_BACKUP_DEST", DEFAULT_DEST))
    parser.add_argument(
        "--keep",
        type=int,
        default=int(os.environ.get("ALFRED_BACKUP_KEEP", str(DEFAULT_KEEP))),
    )
    parser.add_argument("--stamp", default=os.environ.get("ALFRED_BACKUP_STAMP", _stamp()))
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("ALFRED_BACKUP_OUTPUT_DIR", str(ALFRED_HOME / "backups" / "cold")),
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        default=(os.environ.get("ALFRED_BACKUP_LOCAL_ONLY", "").strip().lower() in TRUE_VALUES),
        help="create the archive but skip S3 upload and retention",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        default=(os.environ.get("ALFRED_BACKUP_PRUNE", "1").strip().lower() in FALSE_VALUES),
        help="skip remote retention pruning after upload",
    )
    parser.add_argument("--no-confirm", action="store_true", help="skip confirmation notification")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with_lock(AGENT)

    # Opt-in: a scheduled backup with no destination and no --local-only is a
    # safe no-op, so the job can be scheduled disarmed and armed later purely
    # through ALFRED_BACKUP_DEST.
    if not args.local_only and not (args.dest or "").strip():
        if doctor_mode():
            print(f"[{AGENT.upper()}-DOCTOR-OK]")
            return 0
        print(
            f"[{AGENT}] no backup destination configured "
            "(set ALFRED_BACKUP_DEST / --dest, or pass --local-only); nothing to do"
        )
        return 0

    try:
        if args.local_only:
            preflight(PreflightSpec(agent=AGENT, bins=[], require_gh_auth=False))
        else:
            preflight(PREFLIGHT)
    except PreflightFailed:
        return 0

    if doctor_mode():
        if not args.local_only:
            try:
                remote_permission_smoke(args.dest, require_retention=not args.no_prune)
            except Exception as exc:
                print(
                    f"[{AGENT.upper()}-PREFLIGHT-FAILED] 1 issue(s):\n"
                    f"  - backup destination check failed: {exc}"
                )
                return 0
        print(f"[{AGENT.upper()}-DOCTOR-OK]")
        return 0

    with tempfile.TemporaryDirectory(prefix="alfred-cold-backup-") as tmp:
        output_dir = Path(args.output_dir or tmp)
        archive, included = create_archive(output_dir=output_dir, stamp=args.stamp)
        if not included:
            msg = "cold-backup: no state, cron, or LaunchAgent files found to archive"
            print(msg, file=sys.stderr)
            slack_post(msg, severity="warn")
            return 1

        if args.local_only:
            print(f"[cold-backup] archive created: {archive}")
            return 0

        try:
            target = upload_archive(archive, args.dest)
            pruned = [] if args.no_prune else prune_remote_backups(args.dest, keep=args.keep)
        except Exception as exc:
            msg = f"cold-backup failed: {exc}"
            print(msg, file=sys.stderr)
            slack_post(msg, severity="alert")
            return 1

        retention = (
            "retention pruning skipped" if args.no_prune else f"pruned {len(pruned)} old backup(s)"
        )
        msg = f"cold-backup uploaded {target}; included {len(included)} root item(s); {retention}."
        print(f"[cold-backup] {msg}")
        if not args.no_confirm:
            confirm(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
