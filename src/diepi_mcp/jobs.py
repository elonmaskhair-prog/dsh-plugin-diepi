"""Persistent, path-private asynchronous job management."""

from __future__ import annotations

import atexit
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Iterator, Mapping
import uuid

from . import __version__
from .artifact_binding import artifact_binding_errors
from .config import AdapterConfig, DatasetConfig
from .integration import ArtifactStore
from .models import BacktestSpec, MaCrossoverSpec
from .security import confined_child, is_run_id, require_job_id
from .strategy import canonical_json, strategy_digest, template_digest


ACTIVE_STATES = ("queued", "running", "cancel_requested")
TERMINAL_STATES = ("succeeded", "canceled", "failed", "interrupted")
ALL_STATES = frozenset((*ACTIVE_STATES, *TERMINAL_STATES))

# These are deliberately conservative process-level safety limits. They remain
# constants until the public configuration schema can be versioned without
# silently changing existing deployments.
JOB_TIMEOUT_SECONDS = 6 * 60 * 60
CANCEL_GRACE_SECONDS = 15.0
TERMINATE_GRACE_SECONDS = 5.0
PROCESS_POLL_SECONDS = 0.1
MAX_WORKER_RESULT_BYTES = 1024 * 1024
MAX_PRIVATE_REQUEST_BYTES = 256 * 1024
MAX_VALIDATION_REPORT_BYTES = 4 * 1024 * 1024
MAX_WORKER_ERROR_MESSAGE_CHARS = 2000

# The backtest is local and must not inherit credentials, Python import hooks,
# proxy settings, or dynamic-loader overrides from the MCP host.
_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "WINDIR",
    }
)
_DIGEST_CHARS = frozenset("0123456789abcdef")
_ERROR_CODE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SUBMISSION_ID_RE = re.compile(r"^req_[A-Za-z0-9_-]{8,64}$")
_DATASET_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

PUBLIC_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "dataset_id",
        "strategy",
        "backtest",
        "resolved_price_mode",
        "adapter_version",
        "template_sha256",
        "validation_report_sha256",
        "validation_sources_sha256",
        "validation_start_date",
    }
)
PRIVATE_REQUEST_KEYS = frozenset(
    {
        *PUBLIC_REQUEST_KEYS,
        "run_id",
        "data_root",
        "results_root",
        "request_digest",
        "strategy_digest",
        "template_digest",
    }
)
VALIDATION_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "contract_ready",
        "scope",
        "dataset_kind",
        "manifest_status",
        "manifest_sha256",
        "calendar",
        "pair_reports",
        "issues",
        "limitations",
        "report_sha256",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _is_digest(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= _DIGEST_CHARS


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON object contains a duplicate key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON contains non-finite constant {value}")


def _plain_file_signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        getattr(metadata, "st_nlink", 1),
        metadata.st_size,
        getattr(metadata, "st_mtime_ns", 0),
        getattr(metadata, "st_ctime_ns", 0),
        getattr(metadata, "st_file_attributes", 0),
    )


def _plain_file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        getattr(metadata, "st_nlink", 1),
        getattr(metadata, "st_file_attributes", 0),
    )


def _is_plain_single_link_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not bool(
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
        and getattr(metadata, "st_nlink", 1) == 1
    )


def _read_bounded_json_object(path: Path, limit: int, label: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is missing") from exc
    if not _is_plain_single_link_file(metadata):
        raise ValueError(f"{label} must be a regular file")
    if metadata.st_size < 1 or metadata.st_size > limit:
        raise ValueError(f"{label} exceeds its size limit")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} cannot be read") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not _is_plain_single_link_file(opened)
            or _plain_file_identity(opened) != _plain_file_identity(metadata)
            or opened.st_size != metadata.st_size
        ):
            raise ValueError(f"{label} changed before it could be read safely")

        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        stable = os.fstat(descriptor)
        if (
            _plain_file_signature(stable) != _plain_file_signature(opened)
            or len(raw) != opened.st_size
        ):
            raise ValueError(f"{label} changed while it was being read")
        try:
            final_path = path.lstat()
        except OSError as exc:
            raise ValueError(f"{label} changed while it was being read") from exc
        if (
            not _is_plain_single_link_file(final_path)
            or _plain_file_identity(final_path) != _plain_file_identity(opened)
            or final_path.st_size != opened.st_size
        ):
            raise ValueError(f"{label} changed while it was being read")
    except OSError as exc:
        raise ValueError(f"{label} cannot be read") from exc
    finally:
        os.close(descriptor)
    if len(raw) > limit:
        raise ValueError(f"{label} exceeds its size limit")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if type(payload) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _cancel_requested(path: Path) -> bool:
    """Treat any directory entry as a fail-safe cancel signal without following it."""

    return os.path.lexists(path)


def _create_cancel_request(path: Path) -> None:
    """Create an empty plain marker without following a pre-positioned link."""

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOINHERIT", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RuntimeError("cancel marker cannot be inspected") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or bool(
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            or getattr(metadata, "st_nlink", 1) != 1
            or metadata.st_size != 0
        ):
            raise RuntimeError("cancel marker is not a plain empty file")
        return
    try:
        os.close(descriptor)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def build_public_request(
    dataset_id: str,
    strategy: MaCrossoverSpec,
    backtest: BacktestSpec,
    *,
    resolved_price_mode: str,
    validation_report_sha256: str | None = None,
    validation_sources_sha256: str | None = None,
    validation_start_date: str | None = None,
) -> dict[str, Any]:
    """Build the exact execution-request preimage used for idempotency and artifacts."""

    return {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "strategy": strategy.model_dump(mode="json"),
        "backtest": backtest.model_dump(mode="json"),
        "resolved_price_mode": resolved_price_mode,
        "adapter_version": __version__,
        "template_sha256": template_digest(),
        "validation_report_sha256": validation_report_sha256,
        "validation_sources_sha256": validation_sources_sha256,
        "validation_start_date": validation_start_date,
    }


def request_digest(public_request: Mapping[str, Any]) -> str:
    """Hash one exact public execution-request preimage."""

    if type(public_request) is not dict or set(public_request) != PUBLIC_REQUEST_KEYS:
        raise ValueError("public execution request does not match schema v1")
    if public_request.get("schema_version") != 1:
        raise ValueError("public execution request does not match schema v1")
    return hashlib.sha256(canonical_json(public_request)).hexdigest()


def _validated_validation_report(
    payload: Mapping[str, Any], expected_sha256: str | None = None
) -> dict[str, Any]:
    """Return a detached canonical report after authenticating its embedded digest."""

    if type(payload) is not dict or set(payload) != VALIDATION_REPORT_KEYS:
        raise ValueError("validation report does not match schema v1")
    if (
        payload.get("schema_version") != 1
        or payload.get("status") != "pass"
        or payload.get("contract_ready") is not True
    ):
        raise ValueError("validation report is not contract-ready schema v1 evidence")
    supplied = payload.get("report_sha256")
    if not _is_digest(supplied):
        raise ValueError("validation report contains an invalid report_sha256")
    preimage = {key: payload[key] for key in payload if key != "report_sha256"}
    observed = hashlib.sha256(canonical_json(preimage)).hexdigest()
    if observed != supplied or (expected_sha256 is not None and observed != expected_sha256):
        raise ValueError("validation report digest does not match its canonical payload")
    # The JSON round trip both detaches nested caller-owned containers and
    # proves every value is finite and serializable before admission.
    return json.loads(canonical_json(dict(payload)).decode("utf-8"))


def load_validation_report(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Load one persisted canonical validation-report preimage."""

    payload = _read_bounded_json_object(path, MAX_VALIDATION_REPORT_BYTES, "validation report")
    return _validated_validation_report(payload, expected_sha256)


def load_private_request(path: Path) -> dict[str, Any]:
    """Load and authenticate a bounded, exact-schema private worker request."""

    payload = _read_bounded_json_object(path, MAX_PRIVATE_REQUEST_BYTES, "worker request")
    if set(payload) != PRIVATE_REQUEST_KEYS or payload.get("schema_version") != 1:
        raise ValueError("worker request does not match private schema v1")
    dataset_id = payload.get("dataset_id")
    if type(dataset_id) is not str or not _DATASET_ID_RE.fullmatch(dataset_id):
        raise ValueError("worker request contains an invalid dataset_id")
    run_id = payload.get("run_id")
    if not is_run_id(run_id):
        raise ValueError("worker request contains an invalid run_id")
    if payload.get("resolved_price_mode") not in {"dual", "raw", "hfq"}:
        raise ValueError("worker request contains an invalid resolved_price_mode")
    if (
        type(payload.get("adapter_version")) is not str
        or not 1 <= len(payload["adapter_version"]) <= 64
    ):
        raise ValueError("worker request contains an invalid adapter_version")
    for key in ("request_digest", "strategy_digest", "template_digest", "template_sha256"):
        if not _is_digest(payload.get(key)):
            raise ValueError(f"worker request contains an invalid {key}")
    validation_digest = payload.get("validation_report_sha256")
    if validation_digest is not None and not _is_digest(validation_digest):
        raise ValueError("worker request contains an invalid validation_report_sha256")
    validation_sources = payload.get("validation_sources_sha256")
    if validation_sources is not None and not _is_digest(validation_sources):
        raise ValueError("worker request contains an invalid validation_sources_sha256")
    validation_start = payload.get("validation_start_date")
    if validation_start is not None:
        if type(validation_start) is not str or not re.fullmatch(r"[0-9]{8}", validation_start):
            raise ValueError("worker request contains an invalid validation_start_date")
        try:
            datetime.strptime(validation_start, "%Y%m%d")
        except ValueError as exc:
            raise ValueError("worker request contains an invalid validation_start_date") from exc
    for key in ("data_root", "results_root"):
        value = payload.get(key)
        if type(value) is not str or not value or not Path(value).is_absolute():
            raise ValueError(f"worker request contains an invalid {key}")
    if payload["template_digest"] != payload["template_sha256"]:
        raise ValueError("worker request template digests disagree")
    public = {key: payload[key] for key in PUBLIC_REQUEST_KEYS}
    if request_digest(public) != payload["request_digest"]:
        raise ValueError("worker request digest does not match its execution request")
    return payload


class _InstanceLock:
    """One adapter process owns one state root at a time."""

    def __init__(self, path: Path):
        self._handle = path.open("a+b")
        self._handle.seek(0, os.SEEK_END)
        if self._handle.tell() == 0:
            self._handle.write(b"\0")
            self._handle.flush()
        self._handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - exercised by Linux CI
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            raise RuntimeError("another diepi-mcp process owns this state_root") from exc
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - exercised by Linux CI
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()


class JobStore:
    def __init__(self, path: Path):
        self.path = confined_child(path.parent, path.name, "job database")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    dataset_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    strategy_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    exit_code INTEGER,
                    terminal_status TEXT,
                    error_code TEXT,
                    submission_id TEXT,
                    artifact_manifest_sha256 TEXT
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "submission_id" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN submission_id TEXT")
            if "artifact_manifest_sha256" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN artifact_manifest_sha256 TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_submission_id_unique
                ON jobs(submission_id) WHERE submission_id IS NOT NULL
                """
            )
            connection.execute(
                """
                UPDATE jobs SET state = 'interrupted', finished_at = ?,
                    error_code = 'ADAPTER_RESTARTED'
                WHERE state IN ('queued', 'running', 'cancel_requested')
                """,
                (_now(),),
            )

    def _connect(self) -> sqlite3.Connection:
        self._validate_database_file()
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            self._validate_database_file()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            return connection
        except BaseException:
            connection.close()
            raise

    def _validate_database_file(self) -> None:
        if not os.path.lexists(self.path):
            return
        try:
            metadata = self.path.lstat()
        except OSError as exc:
            raise RuntimeError("job database cannot be inspected") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or bool(
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            or getattr(metadata, "st_nlink", 1) != 1
        ):
            raise RuntimeError("job database must be a plain single-link file")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit or roll back a short transaction, then always close it."""

        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _insert(connection: sqlite3.Connection, record: Mapping[str, Any]) -> None:
        connection.execute(
            """
            INSERT INTO jobs (
                job_id, run_id, dataset_id, state, request_digest,
                strategy_digest, created_at, submission_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["job_id"],
                record["run_id"],
                record["dataset_id"],
                record["state"],
                record["request_digest"],
                record["strategy_digest"],
                record["created_at"],
                record.get("submission_id"),
            ),
        )

    def insert(self, record: Mapping[str, Any]) -> None:
        """Insert a record without admission control (primarily for migrations/tests)."""

        with self._connection() as connection:
            self._insert(connection, record)

    def reserve(
        self, record: Mapping[str, Any], active_limit: int
    ) -> tuple[dict[str, Any] | None, bool]:
        """Atomically return an idempotent match or admit and insert a new job."""

        if type(active_limit) is not int or active_limit < 1:
            raise ValueError("active_limit must be a positive integer")
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            submission_id = record.get("submission_id")
            if submission_id is not None:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE submission_id = ?", (submission_id,)
                ).fetchone()
                if existing is not None:
                    if existing["request_digest"] != record["request_digest"]:
                        raise ValueError("submission_id is already bound to a different request")
                    return dict(existing), False
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM jobs WHERE state IN ({placeholders})",
                ACTIVE_STATES,
            ).fetchone()
            if int(row["count"]) >= active_limit:
                return None, False
            self._insert(connection, record)
        return dict(record), True

    def get(self, job_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("unknown job_id")
        return dict(row)

    @staticmethod
    def _validated_update(values: Mapping[str, Any]) -> None:
        allowed = {
            "state",
            "started_at",
            "finished_at",
            "exit_code",
            "terminal_status",
            "error_code",
            "artifact_manifest_sha256",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("invalid job update")
        if "state" in values and values["state"] not in ALL_STATES:
            raise ValueError("invalid job state")
        manifest_sha256 = values.get("artifact_manifest_sha256")
        if manifest_sha256 is not None and not _is_digest(manifest_sha256):
            raise ValueError("invalid artifact manifest digest")

    def update(self, job_id: str, **values: Any) -> None:
        """Update non-state metadata; state changes must use ``transition``."""

        self._validated_update(values)
        if "state" in values:
            raise ValueError("state changes require an expected-state transition")
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*values.values(), job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("unknown job_id")

    def transition(
        self,
        job_id: str,
        from_states: tuple[str, ...] | list[str] | set[str] | frozenset[str],
        **values: Any,
    ) -> bool:
        """Compare-and-swap a job from one of ``from_states`` to new values."""

        self._validated_update(values)
        expected = tuple(dict.fromkeys(from_states))
        if not expected or any(state not in ALL_STATES for state in expected):
            raise ValueError("invalid expected job states")
        assignments = ", ".join(f"{key} = ?" for key in values)
        placeholders = ",".join("?" for _ in expected)
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ? AND state IN ({placeholders})",
                (*values.values(), job_id, *expected),
            )
        return cursor.rowcount == 1

    def active_count(self) -> int:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM jobs WHERE state IN ({placeholders})",
                ACTIVE_STATES,
            ).fetchone()
        return int(row["count"])

    def interrupt_active(self, error_code: str = "ADAPTER_SHUTDOWN") -> None:
        if (
            type(error_code) is not str
            or not 1 <= len(error_code) <= 64
            or not error_code[0].isalpha()
            or not set(error_code) <= _ERROR_CODE_CHARS
        ):
            raise ValueError("invalid interruption error code")
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        with self._connection() as connection:
            connection.execute(
                f"""
                UPDATE jobs SET state = 'interrupted', finished_at = ?, error_code = ?
                WHERE state IN ({placeholders})
                """,
                (_now(), error_code, *ACTIVE_STATES),
            )


def _worker_result(path: Path, exit_code: int) -> tuple[str, str | None, str | None]:
    """Return ``(state, terminal_status, error_code)`` after strict validation."""

    payload = _read_bounded_json_object(path, MAX_WORKER_RESULT_BYTES, "worker result")
    if payload.get("schema_version") != 1 or type(payload.get("ok")) is not bool:
        raise ValueError("worker result does not match schema v1")

    if payload["ok"] is False:
        if set(payload) != {"schema_version", "ok", "error"}:
            raise ValueError("worker error result contains unexpected fields")
        error = payload.get("error")
        if type(error) is not dict or set(error) != {"code", "type", "message"}:
            raise ValueError("worker error result does not match schema v1")
        code = error.get("code")
        error_type = error.get("type")
        message = error.get("message")
        if (
            type(code) is not str
            or not 1 <= len(code) <= 64
            or not code[0].isalpha()
            or not set(code) <= _ERROR_CODE_CHARS
            or type(error_type) is not str
            or not 1 <= len(error_type) <= 200
            or type(message) is not str
            or len(message) > MAX_WORKER_ERROR_MESSAGE_CHARS
        ):
            raise ValueError("worker error result contains invalid fields")
        expected_exit = 4 if code == "CANCEL_BRIDGE_UNAVAILABLE" else 1
        if exit_code != expected_exit:
            raise ValueError("worker error result disagrees with its exit code")
        return "failed", None, code

    if set(payload) != {"schema_version", "ok", "output"}:
        raise ValueError("worker success result contains unexpected fields")
    output = payload.get("output")
    contract = output.get("result_contract") if type(output) is dict else None
    terminal_status = contract.get("status") if type(contract) is dict else None
    if terminal_status not in {"SUCCESS", "PARTIAL", "INVALID", "FAILED", "CANCELED"}:
        raise ValueError("worker output contains an invalid terminal status")

    if terminal_status == "SUCCESS":
        if exit_code != 0 or output.get("artifact_verified") is not True:
            raise ValueError("successful worker output disagrees with its exit code or artifact")
        return "succeeded", terminal_status, None
    if terminal_status == "CANCELED":
        if exit_code != 130 or output.get("artifact_verified") is not True:
            raise ValueError("canceled worker output disagrees with its exit code or artifact")
        return "canceled", terminal_status, None
    if exit_code != 3 or type(output.get("artifact_verified")) is not bool:
        raise ValueError("unsuccessful worker output disagrees with its exit code or artifact")
    return "failed", terminal_status, f"DIEPI_{terminal_status}"


def _validate_job_state_shape(record: Mapping[str, Any]) -> None:
    """Reject individually valid fields that form an impossible durable state."""

    state = record.get("state")
    started_at = record.get("started_at")
    finished_at = record.get("finished_at")
    exit_code = record.get("exit_code")
    terminal_status = record.get("terminal_status")
    error_code = record.get("error_code")
    manifest_sha256 = record.get("artifact_manifest_sha256")

    if state in ACTIVE_STATES:
        if any(
            value is not None
            for value in (
                finished_at,
                exit_code,
                terminal_status,
                error_code,
                manifest_sha256,
            )
        ):
            raise ValueError("active state contains terminal evidence")
        if state == "queued" and started_at is not None:
            raise ValueError("queued state has a start time")
        if state != "queued" and started_at is None:
            raise ValueError("running state has no start time")
        return

    if finished_at is None:
        raise ValueError("terminal state has no finish time")
    if state == "succeeded":
        if not (
            started_at is not None
            and exit_code == 0
            and terminal_status == "SUCCESS"
            and error_code is None
            and _is_digest(manifest_sha256)
        ):
            raise ValueError("succeeded state has inconsistent evidence")
        return
    if state == "canceled":
        before_start = (
            started_at is None
            and exit_code is None
            and terminal_status == "CANCELED_BEFORE_START"
            and error_code is None
            and manifest_sha256 is None
        )
        acknowledged = (
            started_at is not None
            and exit_code == 130
            and terminal_status == "CANCELED"
            and error_code is None
            and _is_digest(manifest_sha256)
        )
        if not (before_start or acknowledged):
            raise ValueError("canceled state has inconsistent evidence")
        return
    if state == "interrupted":
        if not (
            exit_code is None
            and terminal_status is None
            and error_code is not None
            and manifest_sha256 is None
        ):
            raise ValueError("interrupted state has inconsistent evidence")
        return
    if state == "failed":
        if error_code is None:
            raise ValueError("failed state has no error code")
        if terminal_status is None:
            if manifest_sha256 is not None or (started_at is None and exit_code is not None):
                raise ValueError("failed state has inconsistent unbound evidence")
            return
        if not (
            started_at is not None
            and terminal_status in {"PARTIAL", "INVALID", "FAILED"}
            and exit_code == 3
            and _is_digest(manifest_sha256)
        ):
            raise ValueError("failed state has inconsistent result evidence")
        return
    raise ValueError("unknown job state")


class JobManager:
    def __init__(self, config: AdapterConfig):
        self.config = config
        self.config.state_root.mkdir(parents=True, exist_ok=True)
        self._instance_lock = _InstanceLock(self.config.state_root / ".adapter.lock")
        self.jobs_root = self.config.state_root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.config.state_root / "jobs.sqlite3")
        self._queue: queue.Queue[str | None] = queue.Queue(
            maxsize=self.config.max_queued_jobs
        )
        self._processes: dict[str, subprocess.Popen[Any]] = {}
        self._lock = threading.RLock()
        self._lifecycle = threading.Condition(self._lock)
        self._lifecycle_state = "open"
        self._close_in_progress = False
        self._closed = False
        self._threads: list[threading.Thread] = []
        self._atexit_callback = self._close_at_exit
        atexit.register(self._atexit_callback)
        for index in range(self.config.max_concurrent_jobs):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"diepi-job-worker-{index + 1}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def _job_dir(self, job_id: str) -> Path:
        return confined_child(self.jobs_root, require_job_id(job_id), "job directory")

    def start(
        self,
        dataset: DatasetConfig,
        strategy: MaCrossoverSpec,
        backtest: BacktestSpec,
        submission_id: str | None = None,
        *,
        validation_report_sha256: str | None = None,
        validation_report: Mapping[str, Any] | None = None,
        validation_sources_sha256: str | None = None,
        validation_start_date: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("job manager is closed")
        start = datetime.strptime(backtest.start_date, "%Y%m%d")
        end = datetime.strptime(backtest.end_date, "%Y%m%d")
        if (end - start).days > self.config.max_calendar_days:
            raise ValueError("requested interval exceeds configured max_calendar_days")
        if submission_id is not None and not _SUBMISSION_ID_RE.fullmatch(submission_id):
            raise ValueError("submission_id must match req_[A-Za-z0-9_-]{8,64}")
        if validation_report_sha256 is not None and not _is_digest(validation_report_sha256):
            raise ValueError("validation_report_sha256 must be a lowercase SHA-256 digest")
        frozen_validation_report = None
        if validation_report is not None:
            frozen_validation_report = _validated_validation_report(
                validation_report, validation_report_sha256
            )
            validation_report_sha256 = frozen_validation_report["report_sha256"]
        if validation_sources_sha256 is not None and not _is_digest(validation_sources_sha256):
            raise ValueError("validation_sources_sha256 must be a lowercase SHA-256 digest")
        if validation_start_date is not None and not re.fullmatch(
            r"[0-9]{8}", validation_start_date
        ):
            raise ValueError("validation_start_date must use YYYYMMDD")
        if validation_start_date is not None:
            try:
                parsed_validation_start = datetime.strptime(validation_start_date, "%Y%m%d")
            except ValueError as exc:
                raise ValueError("validation_start_date must be a valid date") from exc
            if parsed_validation_start > start:
                raise ValueError("validation_start_date must not follow backtest.start_date")

        resolved_price_mode = backtest.price_mode or dataset.default_price_mode
        public_request = build_public_request(
            dataset.dataset_id,
            strategy,
            backtest,
            resolved_price_mode=resolved_price_mode,
            validation_report_sha256=validation_report_sha256,
            validation_sources_sha256=validation_sources_sha256,
            validation_start_date=validation_start_date,
        )
        request_sha256 = request_digest(public_request)
        strategy_sha256 = strategy_digest(strategy)
        with self._lock:
            if self._closed:
                raise RuntimeError("job manager is closed")
            job_id = "job_" + uuid.uuid4().hex
            run_id = (
                "dsh_"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_")
                + uuid.uuid4().hex[:12]
            )
            private_request = {
                **public_request,
                "run_id": run_id,
                "data_root": str(dataset.data_root),
                "results_root": str(dataset.results_root),
                "request_digest": request_sha256,
                "strategy_digest": strategy_sha256,
                "template_digest": public_request["template_sha256"],
            }
            record = {
                "job_id": job_id,
                "run_id": run_id,
                "dataset_id": dataset.dataset_id,
                "state": "queued",
                "request_digest": request_sha256,
                "strategy_digest": strategy_sha256,
                "created_at": _now(),
                "submission_id": submission_id,
            }
            admitted, created = self.store.reserve(record, self.config.max_queued_jobs)
            if admitted is None:
                raise RuntimeError("job queue is full")
            if not created:
                return self.public_status(admitted)

            job_dir = self._job_dir(job_id)
            try:
                job_dir.mkdir(parents=False, exist_ok=False)
                if frozen_validation_report is not None:
                    _atomic_json(job_dir / "validation-report.json", frozen_validation_report)
                _atomic_json(job_dir / "request.json", private_request)
                self._queue.put_nowait(job_id)
            except queue.Full as exc:
                self.store.transition(
                    job_id,
                    ("queued",),
                    state="failed",
                    finished_at=_now(),
                    error_code="JOB_QUEUE_CAPACITY_EXHAUSTED",
                )
                raise RuntimeError("job queue is full") from exc
            except Exception:
                self.store.transition(
                    job_id,
                    ("queued",),
                    state="failed",
                    finished_at=_now(),
                    error_code="JOB_SUBMISSION_FAILED",
                )
                raise
        return self.public_status(record)

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                if self._closed:
                    self.store.transition(
                        job_id,
                        ("queued",),
                        state="interrupted",
                        finished_at=_now(),
                        error_code="ADAPTER_SHUTDOWN",
                    )
                    continue
                self._execute(job_id)
            except Exception:
                try:
                    self.store.transition(
                        job_id,
                        ACTIVE_STATES,
                        state="failed",
                        finished_at=_now(),
                        error_code="ADAPTER_WORKER_FAILED",
                    )
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    def _close_at_exit(self) -> None:
        try:
            self.close()
        except Exception:
            # The OS releases the process-owned lock at exit. Runtime callers use
            # ``close`` directly and receive an explicit incomplete-shutdown error.
            pass

    def close(self) -> None:
        """Stop every worker before releasing exclusive ownership of the state root."""

        with self._lifecycle:
            while self._close_in_progress:
                self._lifecycle.wait()
            if self._lifecycle_state == "closed":
                return
            self._closed = True
            self._lifecycle_state = "closing"
            self._close_in_progress = True
            processes = tuple(self._processes.values())
            live_threads = tuple(thread for thread in self._threads if thread.is_alive())

        try:
            # Freeze persistent state before terminating children. Workers may
            # otherwise observe a terminated process as a malformed result and
            # overwrite the more accurate shutdown outcome with a generic failure.
            try:
                self.store.interrupt_active()
            except Exception:
                pass

            while True:
                try:
                    queued = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    if queued is not None:
                        try:
                            self.store.transition(
                                queued,
                                ("queued",),
                                state="interrupted",
                                finished_at=_now(),
                                error_code="ADAPTER_SHUTDOWN",
                            )
                        except Exception:
                            # Shutdown must continue far enough to terminate processes
                            # and release the exclusive state-root lock.
                            pass
                finally:
                    self._queue.task_done()

            for process in processes:
                try:
                    self._terminate_process(process)
                except Exception:
                    pass

            sentinel_deadline = time.monotonic() + 2.0
            sentinels = 0
            while (
                sentinels < len(live_threads)
                and time.monotonic() < sentinel_deadline
            ):
                try:
                    self._queue.put(None, timeout=0.05)
                except queue.Full:
                    continue
                sentinels += 1

            join_deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
            for thread in live_threads:
                remaining = join_deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    thread.join(timeout=remaining)
                except RuntimeError:
                    pass

            try:
                self.store.interrupt_active()
            except Exception:
                pass

            with self._lifecycle:
                surviving_threads = tuple(
                    thread for thread in self._threads if thread.is_alive()
                )
                surviving_processes = tuple(
                    process for process in self._processes.values() if process.poll() is None
                )
            if surviving_threads or surviving_processes:
                raise RuntimeError(
                    "job manager shutdown is incomplete; worker ownership is retained"
                )

            try:
                atexit.unregister(self._atexit_callback)
            except Exception:
                pass
            self._instance_lock.close()
        except BaseException:
            with self._lifecycle:
                self._close_in_progress = False
                self._lifecycle.notify_all()
            raise
        else:
            with self._lifecycle:
                self._lifecycle_state = "closed"
                self._close_in_progress = False
                self._lifecycle.notify_all()

    def _child_env(self) -> dict[str, str]:
        clean = {
            key: value for key, value in os.environ.items() if key.upper() in _CHILD_ENV_ALLOWLIST
        }
        return clean

    @staticmethod
    def _terminate_process(process: subprocess.Popen[Any]) -> int:
        """Terminate one isolated worker, escalating to kill after a fixed grace."""

        if process.poll() is not None:
            return int(process.returncode)
        try:
            process.terminate()
        except OSError:
            pass
        try:
            return int(process.wait(timeout=TERMINATE_GRACE_SECONDS))
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
        try:
            return int(process.wait(timeout=TERMINATE_GRACE_SECONDS))
        except subprocess.TimeoutExpired:
            return int(process.returncode) if process.returncode is not None else -1

    def _wait_for_process(
        self, process: subprocess.Popen[Any], cancel_file: Path
    ) -> tuple[int, str | None]:
        started = time.monotonic()
        cancel_seen: float | None = started if _cancel_requested(cancel_file) else None
        while True:
            exit_code = process.poll()
            if exit_code is not None:
                return int(exit_code), None

            now = time.monotonic()
            if now - started >= JOB_TIMEOUT_SECONDS:
                _create_cancel_request(cancel_file)
                return self._terminate_process(process), "JOB_TIMEOUT"
            if _cancel_requested(cancel_file):
                if cancel_seen is None:
                    cancel_seen = now
                elif now - cancel_seen >= CANCEL_GRACE_SECONDS:
                    return self._terminate_process(process), "CANCEL_FORCE_TERMINATED"

            try:
                exit_code = process.wait(timeout=PROCESS_POLL_SECONDS)
            except subprocess.TimeoutExpired:
                continue
            return int(exit_code), None

    def _artifact_manifest_for_commit(self, job_id: str, terminal_status: str) -> str:
        """Verify and bind one artifact before atomically committing job state."""

        canonical = require_job_id(job_id)
        record = self.store.get(canonical)
        self.public_status(record)
        dataset = self.config.dataset(record["dataset_id"])
        request = load_private_request(self._job_dir(canonical) / "request.json")
        expected_validation_sha256 = request.get("validation_report_sha256")
        if expected_validation_sha256 is None:
            raise ValueError("artifact-bearing job has no validation report digest")
        validation_report = load_validation_report(
            self._job_dir(canonical) / "validation-report.json",
            expected_validation_sha256,
        )
        if Path(request["data_root"]).resolve() != dataset.data_root:
            raise ValueError("worker request data_root does not match configured dataset")
        if Path(request["results_root"]).resolve() != dataset.results_root:
            raise ValueError("worker request results_root does not match configured dataset")
        run_root = confined_child(dataset.results_root, record["run_id"], "artifact directory")
        loaded = ArtifactStore.load(run_root)
        if loaded.outcome.result_contract.status.value != terminal_status:
            raise ValueError("artifact terminal status does not match worker result")
        if artifact_binding_errors(loaded, record, request, validation_report):
            raise ValueError("artifact does not bind to its execution request")
        return hashlib.sha256(loaded.manifest.to_json_bytes()).hexdigest()

    def _execute(self, job_id: str) -> None:
        job_id = require_job_id(job_id)
        if not self.store.transition(
            job_id,
            ("queued",),
            state="running",
            started_at=_now(),
            error_code=None,
        ):
            return
        job_dir = self._job_dir(job_id)
        command = [
            sys.executable,
            "-I",
            "-u",
            "-m",
            "diepi_mcp.worker",
            "--request",
            str(job_dir / "request.json"),
            "--result",
            str(job_dir / "worker-result.json"),
            "--cancel-file",
            str(job_dir / "cancel.requested"),
        ]
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        exit_code = -1
        forced_error: str | None = None
        with (
            (job_dir / "stdout.log").open("wb") as stdout,
            (job_dir / "stderr.log").open("wb") as stderr,
        ):
            process = subprocess.Popen(
                command,
                cwd=job_dir,
                env=self._child_env(),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )
            with self._lock:
                self._processes[job_id] = process
                closing = self._closed
            if closing:
                # ``close`` may win the race between dequeue and process
                # registration. In that case it could not have snapshotted this
                # child, so the worker that registered it owns termination.
                self._terminate_process(process)
            try:
                exit_code, forced_error = self._wait_for_process(
                    process, job_dir / "cancel.requested"
                )
            except BaseException:
                self._terminate_process(process)
                raise
            finally:
                with self._lock:
                    self._processes.pop(job_id, None)

        if forced_error is not None:
            self.store.transition(
                job_id,
                ("running", "cancel_requested"),
                state="failed",
                finished_at=_now(),
                exit_code=exit_code,
                terminal_status=None,
                error_code=forced_error,
            )
            return

        result_path = job_dir / "worker-result.json"
        try:
            state, terminal_status, error_code = _worker_result(result_path, exit_code)
        except ValueError:
            self.store.transition(
                job_id,
                ("running", "cancel_requested"),
                state="failed",
                finished_at=_now(),
                exit_code=exit_code,
                terminal_status=None,
                error_code="WORKER_RESULT_INVALID",
            )
            return

        artifact_manifest_sha256: str | None = None
        if terminal_status is not None:
            try:
                artifact_manifest_sha256 = self._artifact_manifest_for_commit(
                    job_id, terminal_status
                )
            except Exception:
                self.store.transition(
                    job_id,
                    ("running", "cancel_requested"),
                    state="failed",
                    finished_at=_now(),
                    exit_code=exit_code,
                    terminal_status=None,
                    error_code="ARTIFACT_COMMIT_FAILED",
                    artifact_manifest_sha256=None,
                )
                return

        if state == "succeeded":
            if self.store.transition(
                job_id,
                ("running",),
                state=state,
                finished_at=_now(),
                exit_code=exit_code,
                terminal_status=terminal_status,
                error_code=error_code,
                artifact_manifest_sha256=artifact_manifest_sha256,
            ):
                return
            self.store.transition(
                job_id,
                ("cancel_requested",),
                state="failed",
                finished_at=_now(),
                exit_code=exit_code,
                terminal_status=None,
                error_code="CANCEL_NOT_ACKNOWLEDGED",
            )
            return
        self.store.transition(
            job_id,
            ("running", "cancel_requested"),
            state=state,
            finished_at=_now(),
            exit_code=exit_code,
            terminal_status=terminal_status,
            error_code=error_code,
            artifact_manifest_sha256=artifact_manifest_sha256,
        )

    def status(self, job_id: str) -> dict[str, Any]:
        canonical = require_job_id(job_id)
        return self.public_status(self.store.get(canonical))

    def cancel(self, job_id: str) -> dict[str, Any]:
        canonical = require_job_id(job_id)
        with self._lifecycle:
            if self._lifecycle_state != "open":
                raise RuntimeError("job manager is closed")
            record = self.store.get(canonical)
            self.public_status(record)
            if record["state"] in TERMINAL_STATES:
                return {**self.public_status(record), "cancel_accepted": False}
            cancel_path = self._job_dir(canonical) / "cancel.requested"
            _create_cancel_request(cancel_path)
            if self.store.transition(
                canonical,
                ("queued",),
                state="canceled",
                finished_at=_now(),
                terminal_status="CANCELED_BEFORE_START",
            ):
                accepted = True
            elif self.store.transition(
                canonical,
                ("running",),
                state="cancel_requested",
            ):
                accepted = True
            else:
                current = self.store.get(canonical)
                if current["state"] in {"cancel_requested", "canceled"}:
                    accepted = True
                else:
                    return {
                        **self.public_status(current),
                        "cancel_accepted": False,
                    }
            return {
                **self.public_status(self.store.get(canonical)),
                "cancel_accepted": accepted,
            }

    @staticmethod
    def public_status(record: Mapping[str, Any]) -> dict[str, Any]:
        try:
            require_job_id(record.get("job_id"))
            if not is_run_id(record.get("run_id")):
                raise ValueError("run_id")
            dataset_id = record.get("dataset_id")
            if type(dataset_id) is not str or not _DATASET_ID_RE.fullmatch(dataset_id):
                raise ValueError("dataset_id")
            if record.get("state") not in ALL_STATES:
                raise ValueError("state")
            if not _is_digest(record.get("request_digest")) or not _is_digest(
                record.get("strategy_digest")
            ):
                raise ValueError("request identity")
            timestamps: dict[str, datetime | None] = {}
            for key in ("created_at", "started_at", "finished_at"):
                value = record.get(key)
                if value is None and key != "created_at":
                    timestamps[key] = None
                    continue
                if type(value) is not str or len(value) > 40:
                    raise ValueError(key)
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError(key)
                timestamps[key] = parsed
            exit_code = record.get("exit_code")
            if exit_code is not None and type(exit_code) is not int:
                raise ValueError("exit_code")
            terminal_status = record.get("terminal_status")
            if terminal_status not in {
                None,
                "SUCCESS",
                "PARTIAL",
                "INVALID",
                "FAILED",
                "CANCELED",
                "CANCELED_BEFORE_START",
            }:
                raise ValueError("terminal_status")
            error_code = record.get("error_code")
            if error_code is not None and (
                type(error_code) is not str
                or not 1 <= len(error_code) <= 64
                or not error_code[0].isalpha()
                or not set(error_code) <= _ERROR_CODE_CHARS
            ):
                raise ValueError("error_code")
            submission_id = record.get("submission_id")
            if submission_id is not None and (
                type(submission_id) is not str
                or not _SUBMISSION_ID_RE.fullmatch(submission_id)
            ):
                raise ValueError("submission_id")
            manifest_sha256 = record.get("artifact_manifest_sha256")
            if manifest_sha256 is not None and not _is_digest(manifest_sha256):
                raise ValueError("artifact_manifest_sha256")
            started = timestamps["started_at"]
            finished = timestamps["finished_at"]
            if started is not None and started < timestamps["created_at"]:
                raise ValueError("started_at precedes created_at")
            if finished is not None and finished < (started or timestamps["created_at"]):
                raise ValueError("finished_at precedes job progress")
            _validate_job_state_shape(record)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("adapter job state is invalid") from exc
        return {
            "job_id": record["job_id"],
            "run_id": record["run_id"],
            "dataset_id": record["dataset_id"],
            "state": record["state"],
            "request_digest": record["request_digest"],
            "strategy_digest": record["strategy_digest"],
            "created_at": record["created_at"],
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "terminal_status": record.get("terminal_status"),
            "error_code": record.get("error_code"),
            "submission_id": record.get("submission_id"),
            "artifact_manifest_sha256": record.get("artifact_manifest_sha256"),
        }


__all__ = [
    "JobManager",
    "JobStore",
    "PRIVATE_REQUEST_KEYS",
    "PUBLIC_REQUEST_KEYS",
    "TERMINAL_STATES",
    "build_public_request",
    "load_private_request",
    "load_validation_report",
    "request_digest",
]
