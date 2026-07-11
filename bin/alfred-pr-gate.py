#!/usr/bin/env python3
"""Fail-closed operator gate for checking or squash-merging one GitHub PR."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

GOOD_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
PENDING_STATES = {"EXPECTED", "IN_PROGRESS", "PENDING", "QUEUED", "REQUESTED", "WAITING"}
CODEX_LOGINS = {"chatgpt-codex-connector", "chatgpt-codex-connector[bot]"}
GREPTILE_LOGINS = {"greptile-apps", "greptile-apps[bot]"}
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)


class GateError(RuntimeError):
    pass


@dataclass(frozen=True)
class GateSnapshot:
    repo: str
    number: int
    head: str
    mergeable: str
    merge_state: str
    unresolved_threads: int
    checks: tuple[str, ...]
    greptile_commit: str | None
    greptile_score: str | None
    codex_commit: str | None
    review_decision: str
    state: str


def _run_json(command: list[str]) -> Any:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown gh failure").strip()
        raise GateError(f"{' '.join(command[:3])} failed: {detail[:500]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GateError(f"{' '.join(command[:3])} returned invalid JSON") from exc


def _reviewed_sha(body: str, marker: str) -> str | None:
    marker_at = body.lower().rfind(marker.lower())
    if marker_at < 0:
        return None
    matches = SHA_RE.findall(body[marker_at : marker_at + 500])
    return max(matches, key=len).lower() if matches else None


def _matches_head(reviewed: str | None, head: str) -> bool:
    return bool(reviewed and len(reviewed) == 40 and reviewed.lower() == head.lower())


def _latest_comment(comments: list[dict[str, Any]], logins: set[str]) -> dict[str, Any] | None:
    matches = [c for c in comments if ((c.get("user") or {}).get("login") or "") in logins]
    return (
        max(matches, key=lambda c: c.get("updated_at") or c.get("created_at") or "")
        if matches
        else None
    )


def _check_states(checks: list[dict[str, Any]]) -> tuple[str, ...]:
    if not checks:
        raise GateError("no CI checks reported")
    names: list[str] = []
    for check in checks:
        name = str(check.get("name") or check.get("context") or "unnamed check")
        status = str(check.get("status") or "").upper()
        conclusion = str(check.get("conclusion") or check.get("state") or "").upper()
        if status in PENDING_STATES or conclusion in PENDING_STATES:
            raise GateError(f"CI pending: {name}")
        if conclusion not in GOOD_CONCLUSIONS:
            raise GateError(f"CI not green: {name} ({conclusion or status or 'unknown'})")
        names.append(name)
    return tuple(sorted(names))


def _graphql_threads(owner: str, name: str, number: int) -> dict[str, Any]:
    query = (
        "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
        "pullRequest(number:$number){headRefOid mergeable mergeStateStatus "
        "reviewThreads(first:100){nodes{isResolved}}}}}"
    )
    payload = _run_json(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ]
    )
    pr = ((payload.get("data") or {}).get("repository") or {}).get("pullRequest")
    if not isinstance(pr, dict):
        raise GateError("GitHub did not return the pull request thread snapshot")
    nodes = ((pr.get("reviewThreads") or {}).get("nodes")) or []
    if len(nodes) >= 100:
        raise GateError("review thread count reached the 100-thread safety limit")
    pr["unresolved_threads"] = sum(1 for node in nodes if not node.get("isResolved"))
    return pr


def _graphql_checks(owner: str, name: str, head: str) -> list[dict[str, Any]]:
    query = (
        "query($owner:String!,$name:String!,$head:GitObjectID!,$after:String){"
        "repository(owner:$owner,name:$name){object(oid:$head){... on Commit{"
        "statusCheckRollup{contexts(first:100,after:$after){nodes{__typename "
        "... on CheckRun{name status conclusion} "
        "... on StatusContext{context state}}"
        "pageInfo{hasNextPage endCursor}}}}}}}"
    )
    checks: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        command = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"head={head}",
        ]
        if after is not None:
            command.extend(["-F", f"after={after}"])
        payload = _run_json(command)
        commit = ((payload.get("data") or {}).get("repository") or {}).get("object")
        rollup = (commit or {}).get("statusCheckRollup")
        contexts = (rollup or {}).get("contexts")
        if not isinstance(contexts, dict):
            raise GateError("GitHub did not return the complete CI check rollup")
        for node in contexts.get("nodes") or []:
            if node.get("__typename") == "StatusContext":
                checks.append(
                    {
                        "name": node.get("context"),
                        "status": "COMPLETED",
                        "conclusion": node.get("state"),
                    }
                )
            else:
                checks.append(node)
        page_info = contexts.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return checks
        next_cursor = page_info.get("endCursor")
        if not next_cursor or next_cursor == after:
            raise GateError("CI check pagination did not advance")
        after = str(next_cursor)


def collect_snapshot(
    repo: str,
    number: int,
    *,
    require_greptile: bool = True,
    require_codex: bool = True,
) -> GateSnapshot:
    if repo.count("/") != 1:
        raise GateError("--repo must be owner/name")
    owner, name = repo.split("/", 1)
    pr = _run_json(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "-R",
            repo,
            "--json",
            "headRefOid,mergeable,mergeStateStatus,reviewDecision,state,statusCheckRollup",
        ]
    )
    head = str(pr.get("headRefOid") or "").lower()
    if len(head) != 40:
        raise GateError("could not verify the 40-character PR head SHA")
    if pr.get("state") != "OPEN":
        raise GateError(f"PR is not open ({pr.get('state') or 'unknown'})")
    review_decision = str(pr.get("reviewDecision") or "")
    if review_decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        raise GateError(f"GitHub review decision is {review_decision}")
    threads = _graphql_threads(owner, name, number)
    if str(threads.get("headRefOid") or "").lower() != head:
        raise GateError("PR head changed while collecting the gate snapshot")
    if threads.get("mergeable") != "MERGEABLE" or threads.get("mergeStateStatus") != "CLEAN":
        raise GateError(
            f"PR is not mergeable clean ({threads.get('mergeable')}/{threads.get('mergeStateStatus')})"
        )
    unresolved = int(threads.get("unresolved_threads") or 0)
    if unresolved:
        raise GateError(f"{unresolved} unresolved review thread(s)")
    checks = _check_states(_graphql_checks(owner, name, head))
    comments = _run_json(["gh", "api", f"repos/{repo}/issues/{number}/comments?per_page=100"])
    if len(comments) >= 100:
        raise GateError("issue comment count reached the 100-comment safety limit")

    greptile = _latest_comment(comments, GREPTILE_LOGINS)
    greptile_body = str((greptile or {}).get("body") or "")
    greptile_commit = _reviewed_sha(greptile_body, "Last reviewed commit")
    score_match = re.search(r"Confidence Score:\s*([0-5]/5)", greptile_body, re.IGNORECASE)
    greptile_score = score_match.group(1) if score_match else None
    if require_greptile and (not _matches_head(greptile_commit, head) or greptile_score != "5/5"):
        raise GateError("Greptile has not signed off 5/5 on exact HEAD")

    reviews = _run_json(["gh", "api", f"repos/{repo}/pulls/{number}/reviews?per_page=100"])
    if len(reviews) >= 100:
        raise GateError("review count reached the 100-review safety limit")
    latest_review_by_login: dict[str, dict[str, Any]] = {}
    for review in reviews:
        login = (review.get("user") or {}).get("login") or ""
        previous = latest_review_by_login.get(login)
        if previous is None or str(review.get("submitted_at") or "") >= str(
            previous.get("submitted_at") or ""
        ):
            latest_review_by_login[login] = review
    blocking_reviews = [
        login
        for login, review in latest_review_by_login.items()
        if str(review.get("state") or "").upper() == "CHANGES_REQUESTED"
    ]
    if blocking_reviews:
        raise GateError(f"changes requested by {', '.join(sorted(blocking_reviews))}")

    codex_evidence: list[tuple[str, str]] = []
    for review in reviews:
        login = (review.get("user") or {}).get("login") or ""
        commit_id = str(review.get("commit_id") or "").lower()
        state = str(review.get("state") or "").upper()
        if login in CODEX_LOGINS and state in {"APPROVED", "COMMENTED"} and len(commit_id) == 40:
            codex_evidence.append((str(review.get("submitted_at") or ""), commit_id))
    for comment in comments:
        login = (comment.get("user") or {}).get("login") or ""
        if login in CODEX_LOGINS:
            reviewed = _reviewed_sha(str(comment.get("body") or ""), "Reviewed commit")
            if reviewed and len(reviewed) == 40:
                codex_evidence.append((str(comment.get("updated_at") or ""), reviewed))
    codex_commit = max(codex_evidence, default=("", ""))[1] or None
    if require_codex and not _matches_head(codex_commit, head):
        raise GateError("Codex has not reviewed exact HEAD")

    return GateSnapshot(
        repo=repo,
        number=number,
        head=head,
        mergeable=str(threads.get("mergeable")),
        merge_state=str(threads.get("mergeStateStatus")),
        unresolved_threads=unresolved,
        checks=checks,
        greptile_commit=greptile_commit,
        greptile_score=greptile_score,
        codex_commit=codex_commit,
        review_decision=review_decision,
        state=str(pr.get("state")),
    )


def merge(snapshot: GateSnapshot) -> str:
    payload = _run_json(
        [
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{snapshot.repo}/pulls/{snapshot.number}/merge",
            "-f",
            "merge_method=squash",
            "-f",
            f"sha={snapshot.head}",
        ]
    )
    if payload.get("merged") is not True:
        raise GateError(str(payload.get("message") or "GitHub refused the merge"))
    return str(payload.get("sha") or "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alfred pr", description=__doc__)
    parser.add_argument("action", choices=("check", "merge"))
    parser.add_argument("number", type=int)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--skip-greptile", action="store_true")
    parser.add_argument("--skip-codex", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kwargs = {
        "require_greptile": not args.skip_greptile,
        "require_codex": not args.skip_codex,
    }
    try:
        first = collect_snapshot(args.repo, args.number, **kwargs)
        if args.action == "check":
            payload = {"ok": True, **asdict(first)}
        else:
            second = collect_snapshot(args.repo, args.number, **kwargs)
            if first != second:
                raise GateError("gate snapshot changed during recheck; refusing merge")
            merge_sha = merge(second)
            payload = {"ok": True, "merged": True, "merge_sha": merge_sha, **asdict(second)}
    except GateError as exc:
        payload = {"ok": False, "error": str(exc), "repo": args.repo, "number": args.number}
        print(json.dumps(payload, indent=2) if args.json else f"BLOCKED: {exc}")
        return 1
    print(
        json.dumps(payload, indent=2)
        if args.json
        else f"PASS: {args.repo}#{args.number} @ {first.head[:10]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
