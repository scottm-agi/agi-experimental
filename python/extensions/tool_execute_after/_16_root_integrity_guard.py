"""
Root Integrity Guard — Post-execution filesystem integrity enforcement.

This is a tool_execute_after extension that provides un-bypassable protection
for framework root config files (package.json, package-lock.json, etc.).

Unlike the pre-execution regex-based ProjectPathEnforcer, this guard checks
ACTUAL filesystem state after each code_execution_tool call and reverts any
unauthorized changes. This catches:
- Python subprocess calls that evade regex
- Shell functions/wrappers
- Heredocs and multi-line script patterns
- Persistent SSH CWD from prior tool calls
- Internal npm install from npx create-next-app

Defense-in-depth: Works alongside _15_project_path_enforcer.py (pre-guard).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.root_integrity_guard")


def _hash_content(content: str) -> str:
    """Compute MD5 hash of string content (change detection, not security)."""
    from python.helpers.hashing import content_hash
    return content_hash(content)


def _hash_file(path: str) -> str:
    """Compute MD5 hash of a file (change detection, not security).
    
    EXEMPTION: Uses raw hashlib.md5 for binary file reads via f.read().
    This cannot use the centralized text-hashing helper because it operates
    on raw bytes, not decoded strings. See RCA-286.
    """
    import hashlib
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


class RootIntegrityGuard(Extension):
    """
    Post-execution guard that reverts unauthorized changes to framework
    root config files after code_execution_tool calls.

    On init:  Snapshots hash + content of protected files (ONCE, globally).
    On after: Compares current state to snapshot, reverts if changed.
    
    IMPORTANT: Uses a class-level global singleton snapshot to prevent
    later agents from re-snapshotting an already-corrupted filesystem.
    The first agent's snapshot is the pristine baseline for ALL agents.
    """

    # Files to protect in the framework root
    PROTECTED_FILES = [
        "package.json",
        "package-lock.json",
        "requirements.txt",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
    ]

    # Tool names this guard monitors
    CODE_EXEC_TOOLS = {"code_execution_tool", "code_execution"}

    # --- Class-level global singleton snapshot ---
    _global_snapshots: dict[str, dict[str, str]] = {}
    _global_snapshot_initialized: bool = False

    def __init__(self, agent):
        super().__init__(agent)
        self._root = self._framework_root()
        
        # Use global singleton: only take snapshot ONCE, on first init
        if not RootIntegrityGuard._global_snapshot_initialized:
            self._take_global_snapshots()
            RootIntegrityGuard._global_snapshot_initialized = True
            protected_count = len(RootIntegrityGuard._global_snapshots)
            if protected_count:
                logger.info(
                    f"[ROOT INTEGRITY GUARD] Global snapshot initialized — "
                    f"protecting {protected_count} files in {self._root}"
                )
        else:
            logger.debug(
                f"[ROOT INTEGRITY GUARD] Reusing existing global snapshot "
                f"for agent {agent.agent_name}"
            )
        
        # Instance-level reference to global snapshots (read-only)
        self._snapshots = RootIntegrityGuard._global_snapshots

    @staticmethod
    def _framework_root() -> str:
        """
        Determine the framework root directory.

        In Docker: /agix/
        Local: the directory containing this file's grandparent
        """
        if os.path.exists("/.dockerenv") or os.path.exists("/agix"):
            return "/agix"
        # Fallback: navigate from extension location
        ext_dir = os.path.dirname(os.path.abspath(__file__))
        # ext_dir = .../python/extensions/tool_execute_after
        return os.path.abspath(os.path.join(ext_dir, "..", "..", ".."))

    def _take_global_snapshots(self):
        """Take a ONE-TIME snapshot of all protected files in the framework root.
        
        This is only called once (by the first agent). The snapshot is stored
        at the class level and shared by all subsequent agent instances.
        """
        RootIntegrityGuard._global_snapshots = {}
        for filename in self.PROTECTED_FILES:
            filepath = os.path.join(self._root, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    RootIntegrityGuard._global_snapshots[filepath] = {
                        "hash": _hash_content(content),
                        "content": content,
                    }
                except (OSError, UnicodeDecodeError) as e:
                    logger.warning(
                        f"[ROOT INTEGRITY GUARD] Could not snapshot "
                        f"{filepath}: {e}"
                    )

    def _take_snapshots(self):
        """Legacy compatibility: delegates to global snapshot.
        
        If called after global init, this is a no-op (snapshots are already global).
        If called before global init (e.g. in tests), takes the snapshot globally.
        """
        if not RootIntegrityGuard._global_snapshot_initialized:
            self._take_global_snapshots()
            RootIntegrityGuard._global_snapshot_initialized = True
        self._snapshots = RootIntegrityGuard._global_snapshots

    def _check_and_revert(self) -> list[str]:
        """
        Check all snapshotted files for changes and revert any that changed.

        Returns:
            List of filenames that were reverted.
        """
        reverted = []

        for filepath, snap in self._snapshots.items():
            filename = os.path.basename(filepath)

            # Check if file was deleted
            if not os.path.exists(filepath):
                logger.warning(
                    f"[ROOT INTEGRITY GUARD] {filename} was DELETED from "
                    f"framework root — restoring from snapshot"
                )
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(snap["content"])
                reverted.append(filename)
                continue

            # Check if file was modified
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    current_content = f.read()
                current_hash = _hash_content(current_content)

                if current_hash != snap["hash"]:
                    logger.warning(
                        f"[ROOT INTEGRITY GUARD] {filename} was MODIFIED in "
                        f"framework root — reverting to snapshot. "
                        f"Hash {snap['hash'][:12]} → {current_hash[:12]}"
                    )
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(snap["content"])
                    reverted.append(filename)

            except (OSError, UnicodeDecodeError) as e:
                logger.error(
                    f"[ROOT INTEGRITY GUARD] Error checking {filename}: {e}"
                )

        return reverted

    async def execute(
        self,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        **kwargs,
    ):
        """
        After a code_execution_tool call, check if any root config files
        were modified and revert them.

        Args:
            tool_name: Name of the tool that just executed
            tool_args: Arguments passed to the tool
            **kwargs: Additional arguments

        Returns:
            None (reverts are applied silently to the filesystem)
        """
        # Only act after code execution tools
        if not tool_name or tool_name.lower() not in self.CODE_EXEC_TOOLS:
            return None

        # Check and revert any changes
        reverted = self._check_and_revert()

        if reverted:
            files_str = ", ".join(reverted)
            logger.warning(
                f"[ROOT INTEGRITY GUARD] Reverted {len(reverted)} file(s) "
                f"in framework root: {files_str}. Agent must install "
                f"dependencies inside the project directory, not /agix/."
            )

        return None
