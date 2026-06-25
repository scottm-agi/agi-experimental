"""Post-execution write ledger re-hash.

RCA-365 F-8a: After code_execution_tool completes, re-hash all
ledger-tracked files whose disk state changed. This keeps ledger
hashes current when toolchain operations (npm install, prisma generate)
modify tracked files.

Follows the same pattern as _16_root_integrity_guard.py.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.post_exec_ledger_rehash")

CODE_EXEC_TOOLS = {"code_execution_tool", "code_execution"}


class PostExecLedgerRehash(Extension):
    # Context-aware: code agents only, code execution
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution"})

    """Re-hash ledger-tracked files after code_execution_tool completes."""

    async def execute(
        self,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        **kwargs,
    ) -> None:
        """After code_execution_tool, re-hash any stale ledger entries.

        Args:
            tool_name: Name of the tool that just executed.
            tool_args: Arguments passed to the tool.
            **kwargs: Additional arguments.

        Returns:
            None (always — never block the extension chain).
        """
        if not tool_name or tool_name.lower() not in CODE_EXEC_TOOLS:
            return None

        project_dir = self.agent.data.get("_active_project_dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            return None

        ledger_path = os.path.join(project_dir, ".write_ledger.json")
        if not os.path.isfile(ledger_path):
            return None  # No ledger to update

        try:
            from python.helpers.write_ledger import WriteLedger

            ledger = WriteLedger()
            rehashed = ledger.rehash_stale_entries(project_dir)
            if rehashed:
                logger.info(
                    f"[POST-EXEC LEDGER] Re-hashed {len(rehashed)} files "
                    f"after code_execution: "
                    f"{[os.path.basename(f) for f in rehashed]}"
                )
        except Exception as e:
            logger.warning(
                f"[POST-EXEC LEDGER] Re-hash failed (non-fatal): {e}"
            )

        return None
