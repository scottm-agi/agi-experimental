from __future__ import annotations
"""
Protected System Write Tool
============================
Gates agent write operations to system-critical directories.
Agents MUST use this tool to modify any system files.
Provides path validation, operation logging, and rollback safety.

Issue: #603
"""
import os
import shutil
import logging
from python.helpers.tool import Tool, Response
from python.helpers import files

logger = logging.getLogger("agix.system_write")

# Paths that agents are NEVER allowed to modify
PROTECTED_PATHS = [
    "python/helpers/api.py",
    "python/helpers/tool.py",
    "python/helpers/extension.py",
    "python/agent.py",
    "python/api/poll.py",
    "run_ui.py",
    "initialize.py",
    "docker/",
    ".git/",
    ".env",
]

# Directories where agents ARE allowed to make changes
ALLOWED_DIRECTORIES = [
    "python/tools/",
    "python/extensions/",
    "prompts/",
    "usr/",
    "tmp/",
    "agents/",
    "helpers/dynamic/",
    "webui/components/",
]


def is_path_allowed(path: str) -> tuple[bool, str]:
    """
    Check if a path is safe for agent writes.
    
    Returns:
        (allowed, reason) tuple
    """
    # Normalize path
    normalized = path.replace("\\", "/")
    
    # Check protected paths first (blocklist)
    for protected in PROTECTED_PATHS:
        if protected in normalized:
            return False, f"Path '{path}' is protected: contains '{protected}'"
    
    # Check if path is within allowed directories
    for allowed in ALLOWED_DIRECTORIES:
        if allowed in normalized:
            return True, f"Path is within allowed directory '{allowed}'"
    
    # Default deny for unknown paths
    return False, f"Path '{path}' is not in any allowed directory. Use system_write only for safe directories."


class SystemWrite(Tool):
    """
    Protected system write tool that gates agent modifications
    to the AGIX runtime. Validates paths, logs operations,
    and provides safety for system-critical files.
    """

    async def execute(self, **kwargs) -> Response:
        operation = self.args.get("operation", "write")  # write, append, delete
        path = self.args.get("path", "")
        content = self.args.get("content", "")
        reason = self.args.get("reason", "")
        
        if not path:
            return Response(
                message="Error: 'path' argument is required.",
                break_loop=False
            )
        
        if not reason:
            return Response(
                message="Error: 'reason' argument is required. Explain why this system change is needed.",
                break_loop=False
            )
        
        # Resolve to absolute path
        if not os.path.isabs(path):
            abs_path = files.get_abs_path(path)
        else:
            abs_path = path
        
        # Validate path safety
        allowed, validation_msg = is_path_allowed(path)
        if not allowed:
            logger.warning(f"BLOCKED system write: {path} — {validation_msg}")
            return Response(
                message=f"🛡️ BLOCKED: {validation_msg}\n\nUse code_execution or other tools for non-system paths.",
                break_loop=False
            )
        
        try:
            if operation == "write":
                # Create backup if file exists
                if os.path.exists(abs_path):
                    backup_path = abs_path + ".bak"
                    shutil.copy2(abs_path, backup_path)
                    logger.info(f"Backup created: {backup_path}")
                
                # Create parent dirs if needed
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(content)
                
                logger.info(f"System write: {path} — Reason: {reason}")
                return Response(
                    message=f"✅ System file written: {path}\nReason: {reason}",
                    break_loop=False
                )
            
            elif operation == "append":
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "a", encoding="utf-8") as f:
                    f.write(content)
                
                logger.info(f"System append: {path} — Reason: {reason}")
                return Response(
                    message=f"✅ Content appended to: {path}\nReason: {reason}",
                    break_loop=False
                )
            
            elif operation == "delete":
                if not os.path.exists(abs_path):
                    return Response(
                        message=f"File not found: {path}",
                        break_loop=False
                    )
                
                # Create backup before delete
                backup_path = abs_path + ".deleted.bak"
                shutil.copy2(abs_path, backup_path)
                os.remove(abs_path)
                
                logger.info(f"System delete: {path} (backup at {backup_path}) — Reason: {reason}")
                return Response(
                    message=f"✅ System file deleted: {path} (backup saved)\nReason: {reason}",
                    break_loop=False
                )
            
            else:
                return Response(
                    message=f"Error: Unknown operation '{operation}'. Use 'write', 'append', or 'delete'.",
                    break_loop=False
                )
        
        except Exception as e:
            logger.error(f"System write failed: {path} — {e}")
            return Response(
                message=f"❌ System write failed: {str(e)}",
                break_loop=False
            )
