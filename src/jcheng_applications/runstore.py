"""Append-only, hash-chained run records outside the repository.

Each run owns one JSONL file. Every record carries ``seq``, ``prev_digest``
and its own ``digest``, so truncation or an edited line is detectable. Files
are created ``0600`` in a ``0700`` directory and never contain a token or a
raw lease id.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any

from .model import canonical_json

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_digest(plan: Any) -> str:
    payload = {
        "run_id": plan.run_id,
        "steps": [
            {
                "index": step.index,
                "name": step.name,
                "kind": step.kind,
                "profile": step.profile,
                "scopes": list(step.scopes),
                "params": step.params,
                "idempotency_key": step.idempotency_key,
            }
            for step in plan.steps
        ],
    }
    return _sha256(canonical_json(payload))


def _report_payload(report: Any) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "recipe_name": report.recipe_name,
        "recipe_version": report.recipe_version,
        "contract_hash": report.contract_hash,
        "operator": report.operator,
        "session_id": report.session_id,
        "state": report.state.value,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "steps": [
            {
                "index": step.index,
                "name": step.name,
                "outcome": step.outcome,
                "reason_code": step.reason_code,
                "message": step.message,
                "intent_id": step.intent_id,
                "idempotency_key": step.idempotency_key,
                "bus_verified": step.bus_verified,
                "safety_state": step.safety_state,
            }
            for step in report.steps
        ],
        "abort_reason": report.abort_reason,
        "stop_error": report.stop_error,
    }


class RunStore:
    """Write one hash-chained JSONL record set per run."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self._lock = RLock()

    def path_for(self, run_id: str) -> Path:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError(f"unsafe run_id {run_id!r}")
        return self.directory / f"{run_id}.jsonl"

    def append(self, run_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            path = self.path_for(run_id)
            seq, prev = self._tail(path)
            record: dict[str, Any] = {
                "seq": seq,
                "kind": kind,
                "payload": payload,
                "prev_digest": prev,
            }
            record["digest"] = _sha256(prev + canonical_json(record))
            line = json.dumps(record, sort_keys=True, separators=(",", ":"))
            created = not path.exists()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if created:
                    os.chmod(path, 0o600)
            return record

    def write_report(
        self,
        report: Any,
        *,
        run_id: str,
        recipe_hash: str,
        plan_hash: str,
        evidence_class: str,
        lease_ref: str | None = None,
        lease_released: bool | None = None,
    ) -> Path:
        path = self.path_for(run_id)
        self.append(
            run_id,
            "run_started",
            {
                "run_id": run_id,
                "recipe_name": report.recipe_name,
                "recipe_version": report.recipe_version,
                "recipe_hash": recipe_hash,
                "plan_hash": plan_hash,
                "contract_hash": report.contract_hash,
                "operator": report.operator,
                "session_id": report.session_id,
                "evidence_class": evidence_class,
                "lease_ref": lease_ref,
            },
        )
        for step in report.steps:
            self.append(
                run_id,
                "step",
                {
                    "index": step.index,
                    "name": step.name,
                    "outcome": step.outcome,
                    "reason_code": step.reason_code,
                    "bus_verified": step.bus_verified,
                    "intent_id": step.intent_id,
                    "idempotency_key": step.idempotency_key,
                },
            )
        payload = _report_payload(report)
        payload["report_hash"] = _sha256(canonical_json(payload))
        payload["lease_released"] = lease_released
        self.append(run_id, "run_finished", payload)
        return path

    def verify(self, run_id: str) -> bool:
        path = self.path_for(run_id)
        if not path.exists():
            return False
        prev = ""
        expected_seq = 1
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            record = json.loads(raw)
            digest = record.pop("digest", None)
            if record.get("seq") != expected_seq or record.get("prev_digest") != prev:
                return False
            if digest != _sha256(prev + canonical_json(record)):
                return False
            prev = digest
            expected_seq += 1
        return expected_seq > 1

    @staticmethod
    def _tail(path: Path) -> tuple[int, str]:
        if not path.exists():
            return 1, ""
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
        if not lines:
            return 1, ""
        last = json.loads(lines[-1])
        seq = last.get("seq")
        digest = last.get("digest")
        if not isinstance(seq, int) or not isinstance(digest, str):
            raise ValueError(f"corrupt run record tail in {path}")
        return seq + 1, digest
