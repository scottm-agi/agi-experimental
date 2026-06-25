"""AgentStateMachine — base class for all AGIX state machines.

RCA-475: Provides a reusable, validated state machine with:
- Declarative transition tables (VALID_STATUSES, VALID_TRANSITIONS)
- Audit log (TransitionRecord) for every state change
- Force-transition escape hatch (with audit trail)
- Rollback to previous state
- JSON-serializable via to_dict()/from_dict()

Subclasses define:
    VALID_STATUSES:    frozenset of allowed status strings
    VALID_TRANSITIONS: dict mapping status → frozenset of valid target statuses
    INITIAL_STATUS:    default starting status
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import ClassVar, Dict, FrozenSet, List, Optional, Tuple


@dataclass
class TransitionRecord:
    """Immutable audit record of a single state transition.
    
    Evidence is a structured dict for JSONL serialization (system order).
    Reason is a human-readable string.
    """
    timestamp: float
    from_status: str
    to_status: str
    reason: str
    source: str = ""
    forced: bool = False
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TransitionRecord":
        return cls(
            timestamp=d.get("timestamp", 0.0),
            from_status=d.get("from_status", d.get("from", "")),
            to_status=d.get("to_status", d.get("to", "")),
            reason=d.get("reason", ""),
            source=d.get("source", ""),
            forced=d.get("forced", False),
            evidence=d.get("evidence", {}),
        )


class AgentStateMachine:
    """Abstract base class for declarative state machines.

    Subclasses MUST define:
        VALID_STATUSES:     frozenset[str]
        VALID_TRANSITIONS:  dict[str, frozenset[str]]
        INITIAL_STATUS:     str
    """

    VALID_STATUSES: ClassVar[FrozenSet[str]] = frozenset()
    VALID_TRANSITIONS: ClassVar[Dict[str, FrozenSet[str]]] = {}
    INITIAL_STATUS: ClassVar[str] = ""

    # WAL (Write-Ahead Log) — SMs opt in by setting WAL_ENABLED = True
    WAL_ENABLED: ClassVar[bool] = False
    WAL_DIR: ClassVar[str] = ""

    def __init__(self, status: str = "", entity_id: str = "", wal_dir: str = "") -> None:
        self._entity_id = entity_id
        self._status = status if status else self.INITIAL_STATUS
        self._history: List[TransitionRecord] = []
        self._wal_dir = wal_dir or self.WAL_DIR

    # ── WAL (Write-Ahead Log) ──────────────────────────────────────

    @property
    def _wal_path(self) -> str:
        """Compute WAL file path: {wal_dir}/{class_name_lower}/{entity_id}.jsonl"""
        if not self._wal_dir or not self._entity_id:
            return ""
        sm_type = self.__class__.__name__.lower()
        # Sanitize entity_id for filesystem
        safe_id = self._entity_id.replace("/", "_").replace("\\", "_").replace(" ", "_")
        return os.path.join(self._wal_dir, sm_type, f"{safe_id}.jsonl")

    def _append_to_wal(self, record: TransitionRecord) -> None:
        """Append transition record to WAL file (JSONL).

        WAL failure must NEVER crash the SM — silently ignored.
        """
        if not self.WAL_ENABLED or not self._wal_path:
            return
        try:
            os.makedirs(os.path.dirname(self._wal_path), exist_ok=True)
            with open(self._wal_path, "a") as f:
                f.write(json.dumps(record.to_dict()) + "\n")
        except Exception:
            pass  # WAL failure must never crash the SM

    @classmethod
    def load_from_wal(cls, entity_id: str, wal_dir: str) -> Optional["AgentStateMachine"]:
        """Reconstruct SM state by replaying WAL file.

        Returns None if WAL file does not exist or is entirely unreadable.
        Skips individual corrupted lines (partial recovery).
        """
        sm_type = cls.__name__.lower()
        safe_id = entity_id.replace("/", "_").replace("\\", "_").replace(" ", "_")
        wal_path = os.path.join(wal_dir, sm_type, f"{safe_id}.jsonl")

        if not os.path.exists(wal_path):
            return None

        instance = cls(entity_id=entity_id, wal_dir=wal_dir)
        records_loaded = 0
        try:
            with open(wal_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = TransitionRecord.from_dict(json.loads(line))
                        if record.source == "rollback":
                            # Mirror live rollback: pop the preceding record
                            if instance._history:
                                instance._history.pop()
                            instance._status = record.to_status
                        else:
                            instance._status = record.to_status
                            instance._history.append(record)
                        records_loaded += 1
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue  # Skip corrupted lines
        except Exception:
            if records_loaded == 0:
                return None

        if records_loaded == 0:
            return None

        return instance

    # ── Properties ──────────────────────────────────────────────────

    @property
    def status(self) -> str:
        return self._status

    # ── Core transition logic ───────────────────────────────────────

    def transition(
        self,
        target: str,
        reason: str = "",
        source: str = "",
        force: bool = False,
        evidence: dict | None = None,
    ) -> Tuple[bool, str]:
        """Attempt to transition to *target* status.

        Args:
            target: The desired new status.
            reason: Human-readable reason for the transition.
            source: Source identifier (file, function, agent).
            force:  If True, bypass transition-table validation
                    (but still require target ∈ VALID_STATUSES).

        Returns:
            (True, message) on success, (False, message) on rejection.
        """
        # Gate 1: target must be a known status (even for force)
        if target not in self.VALID_STATUSES:
            msg = (
                f"[{self._entity_id}] REJECTED {self._status}→{target}: "
                f"'{target}' is not a valid status"
            )
            return False, msg

        # Gate 2: transition must be allowed (unless forced)
        if not force:
            allowed = self.VALID_TRANSITIONS.get(self._status, frozenset())
            if target not in allowed:
                msg = (
                    f"[{self._entity_id}] REJECTED {self._status}→{target}: "
                    f"transition not in allowed set {sorted(allowed)}"
                )
                return False, msg

        # Apply transition
        old = self._status
        self._status = target
        record = TransitionRecord(
            timestamp=time.time(),
            from_status=old,
            to_status=target,
            reason=reason,
            source=source,
            forced=force,
            evidence=evidence or {},
        )
        self._history.append(record)
        self._append_to_wal(record)

        msg = f"[{self._entity_id}] {old}→{target} (reason={reason})"
        return True, msg

    # ── Rollback ────────────────────────────────────────────────────

    def rollback(self) -> Tuple[bool, str]:
        """Revert to the previous status by popping the last history entry.

        Returns:
            (True, message) on success, (False, message) if no history.
        """
        if not self._history:
            return False, f"[{self._entity_id}] cannot rollback: no history"

        last = self._history.pop()
        self._status = last.from_status

        # Write rollback to WAL so replay reconstructs the same final state
        rollback_record = TransitionRecord(
            timestamp=time.time(),
            from_status=last.to_status,
            to_status=last.from_status,
            reason=f"rollback: reverted {last.to_status}→{last.from_status}",
            source="rollback",
            forced=False,
            evidence={"rollback_of": last.to_dict()},
        )
        self._append_to_wal(rollback_record)

        return True, f"[{self._entity_id}] rolled back {last.to_status}→{last.from_status}"

    # ── History / audit ─────────────────────────────────────────────

    def get_history(self) -> List[dict]:
        """Return audit log as list of plain dicts (JSON-safe)."""
        return [
            {
                "from_status": r.from_status,
                "to_status": r.to_status,
                "reason": r.reason,
                "source": r.source,
                "timestamp": r.timestamp,
                "forced": r.forced,
                "evidence": r.evidence,
            }
            for r in self._history
        ]

    # ── Serialization ───────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize the state machine to a JSON-safe dict."""
        return {
            "status": self._status,
            "entity_id": self._entity_id,
            "wal_dir": self._wal_dir,
            "history": self.get_history(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentStateMachine":
        """Deserialize from a dict (restores status + entity_id, not full history)."""
        instance = cls(
            status=d.get("status", ""),
            entity_id=d.get("entity_id", ""),
            wal_dir=d.get("wal_dir", ""),
        )
        # Restore history if present
        for h in d.get("history", []):
            instance._history.append(TransitionRecord.from_dict(h))
        return instance
