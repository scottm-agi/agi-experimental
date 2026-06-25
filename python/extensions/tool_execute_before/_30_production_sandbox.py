"""
Production Sandbox — Tool Execute Before Extension

In production mode, intercepts code_execution_tool calls and blocks
commands that attempt to access system files, extract secrets, or
explore the AGIX codebase.

This is a HARD enforcement layer — it operates deterministically
on the command string, not via LLM instructions, so it cannot be
bypassed through prompt injection.
"""
import logging
import re
from python.helpers.extension import Extension
from python.helpers.errors import InterventionException
from python.helpers import feature_flags

logger = logging.getLogger("guardrails.sandbox")


class ProductionSandbox(Extension):
    """
    Block system file access in production mode.
    Runs at tool_execute_before for code_execution_tool.
    """

    # Protected system path patterns (regex)
    PROTECTED_PATH_PATTERNS = [
        r'/agix/python/',
        r'/agix/prompts/',
        r'/agix/data/',
        r'/agix/agents/',
        r'/agix/instruments/',
        r'/agix/webui/',
        r'/agix/python/',
        r'/agix/prompts/',
        r'/agix/data/',
        r'/agix/agents/',
        r'/agix/instruments/',
        r'/agix/webui/',
        r'/agix/\.env',
        r'/agix/\.env',
    ]

    # Blocked command patterns (regex)
    BLOCKED_COMMAND_PATTERNS = [
        # Reading system files
        r'(?:cat|less|more|head|tail|tac|strings|xxd|hexdump)\s+.*(?:/agix/|/agix/python|/agix/prompts|/agix/data|/agix/agents|/agix/webui)',
        r'(?:cat|less|more|head|tail)\s+.*(?:python/helpers/|python/tools/|python/extensions/|python/api/)',
        r'(?:cat|less|more|head|tail)\s+.*(?:data/settings\.json|\.env)',
        # Listing system directories
        r'(?:ls|dir)\s+.*(?:/agix/|/agix/python|/agix/prompts|/agix/data|/agix/agents)',
        r'(?:ls|dir)\s+.*(?:python/helpers|python/tools|python/extensions|python/api)',
        # Finding system files
        r'find\s+(?:/agix|/agix|/)\s',
        r'find\s+\.\s.*(?:\.py|\.md|\.json)\b',
        r'locate\s+.*(?:\.py|extension|guardrail|content_filter)',
        # Grep/search through system files
        r'(?:grep|rg|ag)\s+.*(?:/agix/|/agix/python|/agix/prompts|/agix/data)',
        r'(?:grep|rg|ag)\s+.*(?:python/helpers|python/tools|python/extensions)',
        # Environment variable extraction
        r'(?:env|printenv|set)\s*(?:\||$)',
        r'echo\s+\$\w*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|API)',
        r'(?:cat|less|more)\s+.*\.env\b',
        # Python system module imports
        r'from\s+python\.(?:helpers|tools|extensions|api)\s+import',
        r'import\s+python\.(?:helpers|tools|extensions|api)',
        r'open\s*\(\s*[\'\"]/(?:agix|agix)/',
        r'open\s*\(\s*[\'\"](?:python/|prompts/|data/settings)',
        # Exfiltration
        r'(?:curl|wget)\s+.*(?:-d\s+@|--data-binary|--post-file)',
        r'nc\s+-[a-z]*l',
        r'/dev/tcp/',
    ]

    _compiled_path_patterns = None
    _compiled_cmd_patterns = None

    @classmethod
    def _get_path_patterns(cls):
        if cls._compiled_path_patterns is None:
            cls._compiled_path_patterns = [
                re.compile(p, re.IGNORECASE) for p in cls.PROTECTED_PATH_PATTERNS
            ]
        return cls._compiled_path_patterns

    @classmethod
    def _get_cmd_patterns(cls):
        if cls._compiled_cmd_patterns is None:
            cls._compiled_cmd_patterns = [
                re.compile(p, re.IGNORECASE) for p in cls.BLOCKED_COMMAND_PATTERNS
            ]
        return cls._compiled_cmd_patterns

    @classmethod
    def is_blocked_command(cls, command: str) -> bool:
        """Check if a command should be blocked in production.
        Static method for use in tests.
        """
        for pattern in cls._get_cmd_patterns():
            if pattern.search(command):
                return True
        return False

    async def execute(self, **kwargs):
        # Only enforce in production
        if not feature_flags.is_production_env():
            return

        tool_name = kwargs.get("tool_name", "")
        tool_args = kwargs.get("tool_args", {})

        # Only intercept code execution tools
        if tool_name not in ("code_execution_tool", "code_execution"):
            return

        code = tool_args.get("code", "")
        if not code:
            return

        # Check against blocked command patterns
        if self.is_blocked_command(code):
            logger.warning(f"[SANDBOX] BLOCKED command in production: {code[:100]}")
            raise InterventionException(
                "🛡️ Security: This command has been blocked because it attempts to access "
                "protected system files. In this environment, you can only access files "
                "within your project directory."
            )

        # Check for protected paths in the code
        for pattern in self._get_path_patterns():
            if pattern.search(code):
                logger.warning(f"[SANDBOX] BLOCKED path access in production: {code[:100]}")
                raise InterventionException(
                    "🛡️ Security: This command has been blocked because it references "
                    "protected system paths. Please work within your project directory."
                )
