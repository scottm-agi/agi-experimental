"""
TraceabilityIndex — Bidirectional REQ-ID ↔ File Mapping
=========================================================

Part of P0-3 Fix: Requirements Pipeline Signal Loss.

Lightweight in-memory index that maps REQ-ID → {test_files, impl_files}
and file_path → [REQ-IDs] for instant forward and reverse lookups.

Usage:
    index = TraceabilityIndex()
    index.add_requirement("REQ-a1b2c3d4", "Stripe payment integration")
    index.register("REQ-a1b2c3d4", "__tests__/stripe.test.ts", "test")
    index.register("REQ-a1b2c3d4", "src/lib/stripe.ts", "impl")

    # Forward lookup
    files = index.get_files_for_req("REQ-a1b2c3d4")
    # files == {"test_files": ["__tests__/stripe.test.ts"], "impl_files": ["src/lib/stripe.ts"]}

    # Reverse lookup
    reqs = index.get_reqs_for_file("src/lib/stripe.ts")
    # reqs == ["REQ-a1b2c3d4"]

    # Persistence
    index.persist("/path/to/project")
    loaded = TraceabilityIndex.load("/path/to/project")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.traceability")

_PERSIST_FILENAME = "traceability_index.json"
_PERSIST_DIR = ".agix.proj"


class TraceabilityIndex:
    """Bidirectional requirement ↔ file traceability index.

    Maintains two maps:
    - Forward: REQ-ID → {text, test_files, impl_files}
    - Reverse: file_path → [REQ-IDs]
    """

    def __init__(self) -> None:
        # Forward map: req_id → {"text": str, "test_files": list, "impl_files": list}
        self._forward: Dict[str, Dict[str, Any]] = {}
        # Reverse map: file_path → set of req_ids
        self._reverse: Dict[str, set] = {}

    def add_requirement(self, req_id: str, text: str = "") -> None:
        """Register a requirement in the index (without any file links yet).

        Args:
            req_id: The requirement ID (e.g., "REQ-a1b2c3d4")
            text: The requirement description text
        """
        if req_id not in self._forward:
            self._forward[req_id] = {
                "text": text,
                "test_files": [],
                "impl_files": [],
            }
        elif text and not self._forward[req_id].get("text"):
            self._forward[req_id]["text"] = text

    def register(self, req_id: str, file_path: str, file_type: str) -> None:
        """Register a file as linked to a requirement.

        Args:
            req_id: The requirement ID
            file_path: Path to the file (relative to project root)
            file_type: Either "test" or "impl"
        """
        # Ensure req exists in forward map
        if req_id not in self._forward:
            self._forward[req_id] = {
                "text": "",
                "test_files": [],
                "impl_files": [],
            }

        entry = self._forward[req_id]
        target_list = "test_files" if file_type == "test" else "impl_files"

        # Prevent duplicates
        if file_path not in entry[target_list]:
            entry[target_list].append(file_path)

        # Update reverse map
        if file_path not in self._reverse:
            self._reverse[file_path] = set()
        self._reverse[file_path].add(req_id)

    def get_files_for_req(self, req_id: str) -> Dict[str, List[str]]:
        """Forward lookup: get all files linked to a requirement.

        Args:
            req_id: The requirement ID

        Returns:
            Dict with test_files and impl_files lists.
            Returns empty lists if req_id not found.
        """
        entry = self._forward.get(req_id, {})
        return {
            "test_files": list(entry.get("test_files", [])),
            "impl_files": list(entry.get("impl_files", [])),
        }

    def get_reqs_for_file(self, file_path: str) -> List[str]:
        """Reverse lookup: get all REQ-IDs associated with a file.

        Args:
            file_path: The file path to look up

        Returns:
            List of REQ-IDs associated with the file.
        """
        return sorted(self._reverse.get(file_path, set()))

    def coverage_report(self) -> Dict[str, Any]:
        """Generate a coverage report: traced vs untraced requirements.

        A requirement is "traced" if it has at least one test_file or impl_file.

        Returns:
            Dict with total, traced, untraced counts and untraced_ids list.
        """
        total = len(self._forward)
        traced_ids = []
        untraced_ids = []

        for req_id, entry in self._forward.items():
            if entry.get("test_files") or entry.get("impl_files"):
                traced_ids.append(req_id)
            else:
                untraced_ids.append(req_id)

        return {
            "total": total,
            "traced": len(traced_ids),
            "untraced": len(untraced_ids),
            "traced_ids": sorted(traced_ids),
            "untraced_ids": sorted(untraced_ids),
        }

    def to_json(self) -> str:
        """Serialize the index to a JSON string.

        Returns:
            JSON string representation of the forward map.
        """
        # Convert reverse map sets to lists for JSON serialization
        serializable = {}
        for req_id, entry in self._forward.items():
            serializable[req_id] = {
                "text": entry.get("text", ""),
                "test_files": entry.get("test_files", []),
                "impl_files": entry.get("impl_files", []),
            }
        return json.dumps(serializable, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "TraceabilityIndex":
        """Deserialize a TraceabilityIndex from a JSON string.

        Args:
            json_str: JSON string as produced by to_json()

        Returns:
            A new TraceabilityIndex with the deserialized data.
        """
        index = cls()
        data = json.loads(json_str)

        for req_id, entry in data.items():
            index.add_requirement(req_id, entry.get("text", ""))
            for test_file in entry.get("test_files", []):
                index.register(req_id, test_file, "test")
            for impl_file in entry.get("impl_files", []):
                index.register(req_id, impl_file, "impl")

        return index

    def persist(self, project_dir: str) -> None:
        """Persist the traceability index to disk.

        Writes to {project_dir}/.agix.proj/traceability_index.json

        Args:
            project_dir: Root of the project directory
        """
        output_dir = os.path.join(project_dir, _PERSIST_DIR)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, _PERSIST_FILENAME)

        try:
            with open(output_path, "w") as f:
                f.write(self.to_json())
            logger.info(
                f"[TRACEABILITY] Persisted index to {output_path} "
                f"({len(self._forward)} requirements)"
            )
        except Exception as e:
            logger.warning(
                f"[TRACEABILITY] Failed to persist index to {output_path}: {e}"
            )

    @classmethod
    def load(cls, project_dir: str) -> Optional["TraceabilityIndex"]:
        """Load a TraceabilityIndex from disk.

        Args:
            project_dir: Root of the project directory

        Returns:
            TraceabilityIndex if file exists, None otherwise.
        """
        input_path = os.path.join(project_dir, _PERSIST_DIR, _PERSIST_FILENAME)
        if not os.path.exists(input_path):
            return None

        try:
            with open(input_path, "r") as f:
                json_str = f.read()
            index = cls.from_json(json_str)
            logger.info(
                f"[TRACEABILITY] Loaded index from {input_path} "
                f"({len(index._forward)} requirements)"
            )
            return index
        except Exception as e:
            logger.warning(
                f"[TRACEABILITY] Failed to load index from {input_path}: {e}"
            )
            return None


def seed_traceability_from_ledger(agent_data: dict, project_dir: str) -> None:
    """Seed the TraceabilityIndex from the requirements ledger.

    Called during requirements init (Wire Point 2).
    Creates or updates the index with all REQ-IDs from the ledger.
    Idempotent — safe to call multiple times.

    Args:
        agent_data: Agent data dict with _requirements_ledger.
        project_dir: Project directory for persistence.
    """
    ledger = agent_data.get("_requirements_ledger", {})
    requirements = ledger.get("requirements", [])
    if not requirements:
        return

    # Load existing or create new
    index = TraceabilityIndex.load(project_dir) or TraceabilityIndex()

    for req in requirements:
        req_id = req.get("id", "")
        text = req.get("text", "")
        if req_id:
            index.add_requirement(req_id, text)

    index.persist(project_dir)
    logger.info(
        f"[TRACEABILITY] Seeded index with {len(requirements)} requirements"
    )

