"""
Write Ledger — Persistent file write registry for batch delegation.

Root cause (MSR_Smoke_1777055917, RCA-1): Batch subordinates write files
concurrently to the same project directory. Without atomicity guarantees,
a file created by Agent A can be clobbered when Agent B's scaffold or
merge operation overwrites directory state. The lost file is never detected
because no record of the write survives.

This module maintains a persistent `.write_ledger.json` in the project root
that tracks every file written by any subordinate. After batch completion,
the orchestrator calls verify_all() to check every logged file still exists
on disk with the correct checksum.

Thread-safe: Uses file-level locking for concurrent writes.

Usage:
    ledger = WriteLedger()
    ledger.record_write(project_dir, abs_path, agent_id="agent_3")
    result = ledger.verify_all(project_dir)
    # result = {"missing": [...], "present": [...], "corrupted": [...]}
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List

logger = logging.getLogger("agix.write_ledger")

LEDGER_FILENAME = ".write_ledger.json"

# Module-level lock for thread-safe ledger file access
_ledger_lock = threading.Lock()


class WriteLedger:
    """Tracks every file written by any subordinate in a project."""

    def record_write(
        self,
        project_dir: str,
        file_path: str,
        agent_id: str,
    ) -> None:
        """Append a write entry to the project's ledger file.

        Thread-safe: Uses a lock to prevent concurrent JSON corruption.

        Args:
            project_dir: Absolute path to the project root.
            file_path: Absolute path to the file that was written.
            agent_id: Identifier of the agent that performed the write.
        """
        ledger_path = os.path.join(project_dir, LEDGER_FILENAME)

        # Compute checksum if file exists
        checksum = ""
        if os.path.isfile(file_path):
            checksum = self._compute_checksum(file_path)

        entry = {
            "path": file_path,
            "agent_id": agent_id,
            "timestamp": time.time(),
            "checksum": checksum,
            # Legacy compat: also write sha256 key for older ledger readers
            "sha256": checksum,
        }

        with _ledger_lock:
            # Read existing entries
            entries = self._read_ledger(ledger_path)
            entries.append(entry)
            # Write atomically
            self._write_ledger(ledger_path, entries)

        logger.debug(
            f"[WRITE LEDGER] Recorded: {os.path.basename(file_path)} "
            f"by {agent_id} (md5={checksum[:12]}...)"
        )

    def verify_all(self, project_dir: str) -> Dict[str, List[Dict[str, Any]]]:
        """Check every logged file exists and has the correct checksum.

        RCA-273: Deduplicates entries by path, keeping only the LATEST entry
        (by timestamp) per unique file path. This prevents false CORRUPTED
        reports when multiple agents legitimately update the same shared
        mutable file (e.g., decomposition_index.json).

        Args:
            project_dir: Absolute path to the project root.

        Returns:
            Dict with three lists:
                missing: Files that don't exist on disk.
                present: Files that exist with correct checksum.
                corrupted: Files that exist but have wrong checksum.
        """
        ledger_path = os.path.join(project_dir, LEDGER_FILENAME)
        entries = self._read_ledger(ledger_path)

        # ── RCA-273: Deduplicate by path, keep latest entry per file ──
        # When multiple agents write the same file, only the most recent
        # entry's checksum reflects the file's legitimate current state.
        latest_by_path: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            path = entry["path"]
            ts = entry.get("timestamp", 0)
            if path not in latest_by_path or ts > latest_by_path[path].get("timestamp", 0):
                latest_by_path[path] = entry

        deduped_entries = list(latest_by_path.values())

        result: Dict[str, List[Dict[str, Any]]] = {
            "missing": [],
            "present": [],
            "corrupted": [],
        }

        for entry in deduped_entries:
            path = entry["path"]
            recorded_hash = entry.get("checksum", "") or entry.get("sha256", "")

            if not os.path.isfile(path):
                result["missing"].append(entry)
                logger.warning(f"[WRITE LEDGER] ❌ MISSING: {path}")
            elif recorded_hash and self._compute_checksum(path) != recorded_hash:
                result["corrupted"].append(entry)
                logger.warning(f"[WRITE LEDGER] ⚠️ CORRUPTED: {path}")
            else:
                result["present"].append(entry)

        total = len(deduped_entries)
        ok = len(result["present"])
        if total > 0:
            logger.info(
                f"[WRITE LEDGER] Verification: {ok}/{total} OK, "
                f"{len(result['missing'])} missing, "
                f"{len(result['corrupted'])} corrupted"
            )

        return result

    def rehash_stale_entries(self, project_dir: str) -> List[str]:
        """Re-hash any ledger-tracked files whose disk hash differs from recorded hash.

        RCA-365 F-8a: Called after code_execution_tool completes to keep ledger
        current when toolchain operations (npm install, prisma generate) modify
        tracked files outside of write_to_file / replace_in_file.

        Args:
            project_dir: Absolute path to the project root.

        Returns:
            List of file paths that were re-hashed.
        """
        ledger_path = os.path.join(project_dir, LEDGER_FILENAME)

        with _ledger_lock:
            entries = self._read_ledger(ledger_path)
            if not entries:
                return []

            rehashed: List[str] = []

            # Build latest-by-path map (same dedup logic as verify_all)
            latest_by_path: Dict[str, int] = {}
            for i, entry in enumerate(entries):
                path = entry["path"]
                ts = entry.get("timestamp", 0)
                if path not in latest_by_path:
                    latest_by_path[path] = i
                else:
                    prev_ts = entries[latest_by_path[path]].get("timestamp", 0)
                    if ts > prev_ts:
                        latest_by_path[path] = i

            modified = False
            for path, idx in latest_by_path.items():
                if not os.path.isfile(path):
                    continue
                recorded = entries[idx].get("checksum", "") or entries[idx].get("sha256", "")
                if not recorded:
                    continue
                current = self._compute_checksum(path)
                if not current:
                    continue
                if recorded != current:
                    entries[idx]["checksum"] = current
                    entries[idx]["sha256"] = current
                    entries[idx]["timestamp"] = time.time()
                    rehashed.append(path)
                    modified = True
                    logger.info(
                        f"[WRITE LEDGER] Re-hashed: {os.path.basename(path)} "
                        f"(old={recorded[:12]}... new={current[:12]}...)"
                    )

            if modified:
                self._write_ledger(ledger_path, entries)

        return rehashed

    def get_missing_for_redelegation(
        self, project_dir: str
    ) -> List[Dict[str, Any]]:
        """Return re-delegation task definitions for missing files.

        Args:
            project_dir: Absolute path to the project root.

        Returns:
            List of task dicts suitable for call_subordinate_batch:
                [{message: str, original_agent: str, path: str}]
        """
        verification = self.verify_all(project_dir)
        tasks = []

        for entry in verification["missing"]:
            rel_path = os.path.relpath(entry["path"], project_dir)
            tasks.append({
                "message": (
                    f"Re-create the missing file: {rel_path}\n"
                    f"The file was previously written by {entry['agent_id']} "
                    f"but was lost during batch execution. "
                    f"Re-create it with appropriate content based on the "
                    f"project architecture and requirements."
                ),
                "original_agent": entry["agent_id"],
                "path": entry["path"],
            })

        if tasks:
            logger.warning(
                f"[WRITE LEDGER] Generated {len(tasks)} re-delegation tasks "
                f"for missing files"
            )

        return tasks

    def detect_multi_writer_conflicts(
        self, project_dir: str
    ) -> List[Dict[str, Any]]:
        """Return files written by multiple different agents.

        G-6: Scans the full ledger (NOT deduplicated) to find files where
        2+ distinct agent_ids recorded writes. This surfaces cross-agent
        contention that verify_all() hides via deduplication.

        Args:
            project_dir: Absolute path to the project root.

        Returns:
            List of conflict dicts:
                [{path: str, agents: List[str], write_count: int}]
            Only files with 2+ distinct agents are included.
        """
        ledger_path = os.path.join(project_dir, LEDGER_FILENAME)
        entries = self._read_ledger(ledger_path)

        # Group unique agent_ids per file path
        writers_per_file: Dict[str, set] = {}
        for entry in entries:
            path = entry.get("path", "")
            agent_id = entry.get("agent_id", "unknown")
            writers_per_file.setdefault(path, set()).add(agent_id)

        # Return only files with 2+ distinct writers
        conflicts = [
            {
                "path": path,
                "agents": sorted(agents),
                "write_count": len(agents),
            }
            for path, agents in writers_per_file.items()
            if len(agents) > 1
        ]

        if conflicts:
            logger.warning(
                f"[WRITE LEDGER] ⚠️ Multi-writer conflicts detected: "
                f"{len(conflicts)} files written by multiple agents"
            )

        return conflicts

    def clear(self, project_dir: str) -> None:
        """Clear the ledger for a project (e.g., at start of new wave).

        Args:
            project_dir: Absolute path to the project root.
        """
        ledger_path = os.path.join(project_dir, LEDGER_FILENAME)
        with _ledger_lock:
            self._write_ledger(ledger_path, [])

    # ── Private helpers ──

    @staticmethod
    def _compute_checksum(file_path: str) -> str:
        """Compute MD5 checksum of a file's contents (change detection, not security).
        
        NOTE: Exempted from hashing.py centralization — this operates on raw binary
        file contents via incremental update(), not on string fingerprinting.
        """
        h = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, IOError):
            return ""

    @staticmethod
    def _read_ledger(ledger_path: str) -> List[Dict[str, Any]]:
        """Read the ledger JSON file, returning an empty list if not found."""
        if not os.path.isfile(ledger_path):
            return []
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _write_ledger(ledger_path: str, entries: List[Dict[str, Any]]) -> None:
        """Write entries to the ledger JSON file atomically."""
        tmp_path = ledger_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
            os.replace(tmp_path, ledger_path)
        except OSError:
            # If atomic rename fails, try direct write
            with open(ledger_path, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
