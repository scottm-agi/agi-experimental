"""
Generate GUID Tool (ADR-014)

Lightweight native tool that computes content-addressable requirement GUIDs
using MD5 short hashes. Available to ALL agents — no Docker/code_execution needed.

GUID format: REQ-{first 8 chars of MD5 hash}
Input is normalized: stripped + lowercased before hashing.

Usage modes:
  - Single: {"text": "requirement description"} → "REQ-a1b2c3d4"
  - Batch:  {"texts": ["req 1", "req 2"]} → [{"text": "req 1", "id": "REQ-..."}, ...]
"""
from __future__ import annotations

import json
from typing import List

from python.helpers.tool import Tool, Response
from python.helpers.task_hash import compute_task_guid


def compute_guid(text: str) -> str:
    """Compute a stable GUID from requirement text.

    Format: REQ-{md5(normalized)[:8]}
    Deterministic, case-insensitive, whitespace-normalized.
    
    Delegates to the canonical compute_task_guid from task_hash module.
    """
    return compute_task_guid(text)


def compute_guid_batch(texts: List[str]) -> List[str]:
    """Compute GUIDs for a list of texts. Order preserved."""
    return [compute_guid(t) for t in texts]


class GenerateGuid(Tool):
    """Native tool for generating content-addressable requirement GUIDs.

    Supports single and batch modes. No external dependencies.
    """

    async def execute(self, **kwargs) -> Response:
        # Single mode
        text = self.args.get("text", "")
        # Batch mode
        texts = self.args.get("texts", [])

        if texts and isinstance(texts, list):
            # Batch mode — return array of {text, id} pairs
            results = []
            for t in texts:
                results.append({"text": t, "id": compute_guid(t)})
            return Response(
                message=json.dumps(results, indent=2),
                break_loop=False,
            )

        if text:
            # Single mode — return the GUID
            guid = compute_guid(text)
            return Response(
                message=guid,
                break_loop=False,
            )

        return Response(
            message="Error: provide 'text' (single) or 'texts' (batch array)",
            break_loop=False,
        )
