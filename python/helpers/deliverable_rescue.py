"""Post-delegation deliverable rescue (ITR-14 R2-I1).

When a subordinate returns successfully but its expected deliverable file
doesn't exist on disk, this module saves the subordinate's response content
to the expected file path.

Root cause: The researcher agent sometimes returns research content as its
delegation response text instead of using `save_deliverable`. The content is
consumed by the orchestrator but never persisted, breaking downstream phases
that look for the file (e.g., Phase 2 architect looking for
docs/framework-research.md).

Universal: Works for ANY phase/deliverable, not just framework-research.md.
Uses the same PHASE_DELIVERABLES map from requirements.py.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("agix.deliverable_rescue")

# Minimum result length to be considered "real content" worth saving.
# Short results like "Done" or error messages shouldn't be saved as deliverables.
_MIN_CONTENT_LENGTH = 200

# Map from phase seq → expected deliverable paths (relative to project root).
# Only phases where the subordinate is likely to return content inline.
# Phase 1 (scaffold) is excluded because it writes files directly.
_RESCUE_ELIGIBLE_PHASES = {
    "0.5": [os.path.join("docs", "framework-research.md")],
}


def rescue_missing_deliverables(
    project_dir: str,
    profile: str,
    result_text: str,
    delegation_status: str = "success",
) -> list[str]:
    """Save subordinate response to expected deliverable if file is missing.

    Only runs when:
    1. project_dir is set
    2. delegation was successful (not partial/failed)
    3. result_text is long enough to be real content
    4. Expected deliverable file doesn't exist on disk

    Args:
        project_dir: Absolute path to the project directory.
        profile: The subordinate's profile name (e.g., "researcher").
        result_text: The subordinate's full response text.
        delegation_status: "success", "partial", or "failed".

    Returns:
        List of file paths that were rescued (saved).
    """
    if not project_dir or not os.path.isdir(project_dir):
        return []

    if delegation_status != "success":
        return []

    if not result_text or len(result_text) < _MIN_CONTENT_LENGTH:
        return []

    rescued: list[str] = []

    # Check researcher-specific rescue targets
    if profile == "researcher":
        _try_rescue_phase(project_dir, "0.5", result_text, rescued)

    return rescued


def _try_rescue_phase(
    project_dir: str,
    phase_seq: str,
    content: str,
    rescued: list[str],
) -> None:
    """Attempt to rescue deliverables for a specific phase."""
    targets = _RESCUE_ELIGIBLE_PHASES.get(phase_seq, [])

    for rel_path in targets:
        full_path = os.path.join(project_dir, rel_path)

        # Only rescue if file doesn't already exist
        if os.path.isfile(full_path):
            continue

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        try:
            # Add a header noting this was auto-rescued
            header = (
                f"<!-- Auto-rescued from delegation response (deliverable_rescue) -->\n"
                f"<!-- The subordinate returned this content inline instead of using save_deliverable -->\n\n"
            )
            with open(full_path, "w") as f:
                f.write(header + content)

            rescued.append(rel_path)
            logger.info(
                f"[DELIVERABLE RESCUE] Saved missing deliverable: {rel_path} "
                f"({len(content)} chars from subordinate response)"
            )
        except IOError as e:
            logger.warning(
                f"[DELIVERABLE RESCUE] Failed to save {rel_path}: {e}"
            )
