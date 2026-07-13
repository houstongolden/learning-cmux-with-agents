#!/usr/bin/env python3
"""Seal independent orchestrator results and reveal them only as a pair.

Security boundary: this helper provides procedural and capability isolation. It
prevents accidental early reads through its interface, but it does not protect
against a malicious process running as the same OS user, which can inspect or
modify files it owns.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
TOKEN_ENV = "SEALED_RESULTS_TOKEN"
SECURITY_BOUNDARY = (
    "Procedural/capability isolation only; this is not protection against a "
    "malicious process running as the same OS user."
)


class SealedResultsError(RuntimeError):
    """Base error for a sealed-results operation."""


class AlreadySubmittedError(SealedResultsError):
    """Raised when a team attempts to replace its sealed result."""


class AuthenticationError(SealedResultsError):
    """Raised when a team capability is invalid."""


class NotReadyError(SealedResultsError):
    """Raised when reveal is attempted before every expected result exists."""

    def __init__(self, missing: Sequence[str]) -> None:
        self.missing = tuple(missing)
        super().__init__(f"results are still sealed; waiting for: {', '.join(missing)}")


def canonical_json(value: Any) -> bytes:
    """Return a stable UTF-8 JSON representation suitable for hashing."""

    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contract_path(root: Path) -> Path:
    return root / "contract.json"


def _result_path(root: Path, team: str) -> Path:
    return root / "results" / f"{team}.json"


def _write_bytes_and_sync(fd: int, data: bytes) -> None:
    """Write every byte to an unpublished inode and make its contents durable."""

    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("short write while staging sealed result")
        remaining = remaining[written:]
    os.fsync(fd)


def _fsync_directory(path: Path) -> None:
    """Best-effort durability barrier for directory-entry changes."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            # Some supported filesystems do not permit directory fsync.
            pass
    finally:
        os.close(fd)


def _write_once(path: Path, data: bytes, final_mode: int = 0o444) -> None:
    """Publish complete bytes atomically without replacing an existing result."""

    temp_path = path.parent / f".{path.name}.tmp-{secrets.token_hex(16)}"
    fd: int | None = None
    published = False
    try:
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        _write_bytes_and_sync(fd, data)
        os.fchmod(fd, final_mode)
        os.fsync(fd)
        os.close(fd)
        fd = None

        try:
            # A hard link publishes the fully synced inode as one directory
            # operation and, unlike rename(), fails if the final name exists.
            os.link(temp_path, path)
        except FileExistsError as exc:
            raise AlreadySubmittedError(f"refusing to overwrite {path}") from exc
        published = True
        _fsync_directory(path.parent)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # Once linked, the complete read-only final result remains valid;
            # an orphaned private link is cleanup debt, not publication loss.
            if not published:
                raise
        if published:
            _fsync_directory(path.parent)


def initialize(root: Path, teams: Sequence[str]) -> dict[str, Any]:
    """Create a two-team contract and return each capability exactly once."""

    root = Path(root)
    normalized = tuple(teams)
    if len(normalized) != 2 or len(set(normalized)) != 2:
        raise SealedResultsError("exactly two distinct team names are required")
    if any(not team or Path(team).name != team or team in {".", ".."} for team in normalized):
        raise SealedResultsError("team names must be non-empty filename-safe basenames")

    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    (root / "results").mkdir(mode=0o700, exist_ok=True)
    os.chmod(root / "results", 0o700)

    capabilities = {team: secrets.token_urlsafe(32) for team in normalized}
    contract = {
        "schema_version": SCHEMA_VERSION,
        "expected_teams": sorted(normalized),
        "capability_sha256": {
            team: sha256_hex(capabilities[team].encode("utf-8")) for team in sorted(normalized)
        },
        "security_boundary": SECURITY_BOUNDARY,
    }
    _write_once(_contract_path(root), canonical_json(contract))
    return {
        "contract": str(root),
        "capabilities": capabilities,
        "security_boundary": SECURITY_BOUNDARY,
    }


def _load_contract(root: Path) -> dict[str, Any]:
    try:
        contract = json.loads(_contract_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise SealedResultsError(f"invalid or missing contract at {root}") from exc
    if contract.get("schema_version") != SCHEMA_VERSION:
        raise SealedResultsError("unsupported sealed-results contract version")
    teams = contract.get("expected_teams")
    hashes = contract.get("capability_sha256")
    if not isinstance(teams, list) or len(teams) != 2 or not isinstance(hashes, dict):
        raise SealedResultsError("malformed sealed-results contract")
    return contract


def submit(root: Path, team: str, capability: str, payload: Any) -> dict[str, Any]:
    """Authenticate and permanently seal one team's canonical result."""

    root = Path(root)
    contract = _load_contract(root)
    expected_hash = contract["capability_sha256"].get(team)
    supplied_hash = sha256_hex(capability.encode("utf-8"))
    if not isinstance(expected_hash, str) or not hmac.compare_digest(expected_hash, supplied_hash):
        raise AuthenticationError("invalid team or capability token")

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "team": team,
        "payload": payload,
    }
    sealed = canonical_json(envelope)
    path = _result_path(root, team)
    _write_once(path, sealed)
    return {
        "team": team,
        "sha256": sha256_hex(sealed),
        "bytes": len(sealed),
        "path": str(path),
    }


def status(root: Path) -> dict[str, Any]:
    """Report readiness and receipts without exposing sealed payloads."""

    root = Path(root)
    contract = _load_contract(root)
    results: dict[str, Any] = {}
    missing: list[str] = []
    for team in contract["expected_teams"]:
        path = _result_path(root, team)
        if not path.is_file():
            missing.append(team)
            results[team] = {"submitted": False}
            continue
        data = path.read_bytes()
        results[team] = {
            "submitted": True,
            "sha256": sha256_hex(data),
            "bytes": len(data),
            "read_only": not bool(stat.S_IMODE(path.stat().st_mode) & 0o222),
        }
    return {
        "ready": not missing,
        "missing": missing,
        "results": results,
        "security_boundary": SECURITY_BOUNDARY,
    }


def reveal(root: Path) -> dict[str, Any]:
    """Reveal both results atomically at the policy level, or neither result."""

    root = Path(root)
    readiness = status(root)
    if not readiness["ready"]:
        raise NotReadyError(readiness["missing"])

    contract = _load_contract(root)
    revealed: dict[str, Any] = {}
    for team in contract["expected_teams"]:
        path = _result_path(root, team)
        data = path.read_bytes()
        try:
            envelope = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SealedResultsError(f"sealed result for {team} is invalid JSON") from exc
        if envelope.get("team") != team or envelope.get("schema_version") != SCHEMA_VERSION:
            raise SealedResultsError(f"sealed result for {team} has an invalid envelope")
        revealed[team] = {
            "payload": envelope.get("payload"),
            "sha256": sha256_hex(data),
        }
    return {"ready": True, "results": revealed, "security_boundary": SECURITY_BOUNDARY}


def _read_payload(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_token(args: argparse.Namespace) -> str:
    if args.token is not None:
        return args.token
    if args.token_file is not None:
        return Path(args.token_file).read_text(encoding="utf-8").strip()
    token = os.environ.get(TOKEN_ENV)
    if token:
        return token
    raise AuthenticationError(
        f"provide --token-file, --token, or the {TOKEN_ENV} environment variable"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seal two CMUX orchestrator results until both have submitted.",
        epilog=f"SECURITY BOUNDARY: {SECURITY_BOUNDARY}",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    init_parser = subparsers.add_parser(
        "init", help="create a contract and print the two team capabilities once"
    )
    init_parser.add_argument("--root", required=True, type=Path)
    init_parser.add_argument("--teams", required=True, nargs=2, metavar=("TEAM_A", "TEAM_B"))

    submit_parser = subparsers.add_parser("submit", help="seal one team's JSON result once")
    submit_parser.add_argument("--root", required=True, type=Path)
    submit_parser.add_argument("--team", required=True)
    submit_parser.add_argument("--input", required=True, help="JSON file, or - for stdin")
    token_group = submit_parser.add_mutually_exclusive_group()
    token_group.add_argument(
        "--token", help="capability token (prefer --token-file or the environment variable)"
    )
    token_group.add_argument("--token-file", help="file containing the capability token")

    for action, help_text in (
        ("status", "show submission receipts without payloads"),
        ("reveal", "show both payloads only after both teams submit"),
    ):
        action_parser = subparsers.add_parser(action, help=help_text)
        action_parser.add_argument("--root", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "init":
            output = initialize(args.root, args.teams)
        elif args.action == "submit":
            output = submit(args.root, args.team, _resolve_token(args), _read_payload(args.input))
        elif args.action == "status":
            output = status(args.root)
        else:
            output = reveal(args.root)
    except NotReadyError as exc:
        print(canonical_json({"ready": False, "missing": list(exc.missing)}).decode(), end="")
        return 3
    except (SealedResultsError, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(canonical_json(output).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
