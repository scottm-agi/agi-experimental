"""
Content Filter — Deterministic Output Scanner for Integration Responses

Provides modular, rule-based content filtering for AGIX API responses
when the `integration` flag is set. Rules operate on raw text using
pattern matching — NOT LLM-based — so they cannot be bypassed by prompt injection.

Each rule inherits from FilterRule and declares its own patterns.
New rules can be added without modifying existing code.
"""
from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("agix.content_filter")


# ---------------------------------------------------------------------------
# Core Data Types
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    """A single content violation found by a filter rule."""
    rule_name: str
    severity: str  # "low", "medium", "high", "critical"
    description: str
    matched_text: str  # The raw text that triggered the violation (truncated)


@dataclass
class FilterResult:
    """Result of scanning text through the content filter."""
    original: str
    filtered: str
    violations: List[Violation] = field(default_factory=list)
    blocked: bool = False  # True if response should be fully blocked

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


# ---------------------------------------------------------------------------
# Abstract Filter Rule
# ---------------------------------------------------------------------------

class FilterRule(ABC):
    """Base class for modular filter rules."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable rule name."""

    @property
    @abstractmethod
    def severity(self) -> str:
        """Default severity: low, medium, high, critical."""

    @abstractmethod
    def scan(self, text: str) -> tuple[str, List[Violation]]:
        """
        Scan text and return (filtered_text, violations).
        Filtered text has sensitive content redacted.
        """


# ---------------------------------------------------------------------------
# Built-in Rules
# ---------------------------------------------------------------------------

class SecretDetector(FilterRule):
    """Detects potential secrets, API keys, passwords, and env var references."""

    name = "SecretDetector"
    severity = "critical"

    # Common API key / token patterns
    _KEY_PATTERNS = [
        # Generic API key patterns (sk-xxx, key-xxx, etc.)
        r'(?:sk|api|key|token|secret|password|passwd|pwd)[-_]?[a-zA-Z0-9]{20,}',
        # OpenAI keys
        r'sk-[a-zA-Z0-9]{40,}',
        # Bearer tokens
        r'Bearer\s+[a-zA-Z0-9\-._~+/]+=*',
        # Base64-encoded secrets (long base64 strings)
        r'(?:eyJ|YWdl)[a-zA-Z0-9+/]{50,}={0,2}',
        # Generic hex secrets (32+ chars)
        r'(?:0x)?[a-fA-F0-9]{32,}',
    ]

    # Environment variable reference patterns
    _ENV_PATTERNS = [
        r'os\.environ\[[\'"](.*?)[\'"]\]',
        r'os\.environ\.get\([\'"](.*?)[\'"]',
        r'os\.getenv\([\'"](.*?)[\'"]',
        r'\$\{?([A-Z_][A-Z0-9_]{2,})\}?',  # $VAR or ${VAR}
        r'export\s+([A-Z_][A-Z0-9_]+)=',
    ]

    # Known sensitive env var names
    _SENSITIVE_VARS = {
        'API_KEY', 'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'ANTHROPIC_API_KEY',
        'SECRET_KEY', 'DJANGO_SECRET_KEY', 'JWT_SECRET', 'SESSION_SECRET',
        'DATABASE_URL', 'REDIS_URL', 'REDIS_PASSWORD',
        'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
        'GITHUB_TOKEN', 'FORGEJO_TOKEN',
        'SMTP_PASSWORD', 'EMAIL_PASSWORD',
        'ENCRYPTION_KEY', 'PRIVATE_KEY',
        'WEB_PASSWORD', 'AUTH_LOGIN', 'AUTH_PASSWORD',
    }

    def scan(self, text: str) -> tuple[str, List[Violation]]:
        violations = []
        filtered = text

        # Check for env var references with values
        for pattern in self._ENV_PATTERNS:
            for match in re.finditer(pattern, filtered):
                var_name = match.group(1) if match.lastindex else match.group(0)
                if var_name.upper() in self._SENSITIVE_VARS or any(
                    kw in var_name.upper() for kw in ('KEY', 'SECRET', 'PASSWORD', 'TOKEN', 'CREDENTIAL')
                ):
                    violations.append(Violation(
                        rule_name=self.name,
                        severity=self.severity,
                        description=f"Sensitive env var reference: {var_name}",
                        matched_text=match.group(0)[:50]
                    ))
                    filtered = filtered.replace(match.group(0), f"[REDACTED:{var_name}]")

        # Check for API key patterns
        for pattern in self._KEY_PATTERNS:
            for match in re.finditer(pattern, filtered):
                matched = match.group(0)
                # Avoid false positives on short matches or common words
                if len(matched) < 20:
                    continue
                violations.append(Violation(
                    rule_name=self.name,
                    severity=self.severity,
                    description="Potential API key or secret detected",
                    matched_text=matched[:30] + "..."
                ))
                # Redact but keep first 4 chars for identification
                filtered = filtered.replace(matched, matched[:4] + "[REDACTED]")

        return filtered, violations


class PathDetector(FilterRule):
    """Detects container and host filesystem paths that could leak infrastructure details."""

    name = "PathDetector"
    severity = "high"

    # Protected internal paths
    _PROTECTED_PATHS = [
        r'/agix/',
        r'/agix/',
        r'/opt/venv-agix/',
        r'/opt/venv/',
        r'/opt/pyenv/',
        r'/git/agix/',
        r'/root/',
        r'/home/\w+/\.',  # Hidden dirs in home
        r'/etc/(?:shadow|passwd|sudoers)',
        r'/var/run/',
        r'/proc/',
    ]

    # Path patterns that reveal structure
    _STRUCTURE_PATTERNS = [
        r'(?:python|agents)/extensions/\w+/',
        r'python/helpers/\w+\.py',
        r'python/tools/\w+\.py',
        r'python/api/\w+\.py',
        r'prompts/agent\.\w+\.md',
        r'data/settings\.json',
    ]

    def scan(self, text: str) -> tuple[str, List[Violation]]:
        violations = []
        filtered = text

        for pattern in self._PROTECTED_PATHS:
            for match in re.finditer(pattern, filtered):
                violations.append(Violation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=f"Protected path exposed: {match.group(0)}",
                    matched_text=match.group(0)[:50]
                ))
                # Replace with generic path
                filtered = filtered.replace(match.group(0), "[INTERNAL_PATH]/")

        for pattern in self._STRUCTURE_PATTERNS:
            for match in re.finditer(pattern, filtered):
                violations.append(Violation(
                    rule_name=self.name,
                    severity="medium",
                    description=f"Internal structure path: {match.group(0)}",
                    matched_text=match.group(0)[:50]
                ))
                # Keep basename only
                basename = match.group(0).split("/")[-1]
                filtered = filtered.replace(match.group(0), f"[...]{basename}")

        return filtered, violations


class SystemInfoDetector(FilterRule):
    """Detects system prompt content, Docker internals, and configuration leaks."""

    name = "SystemInfoDetector"
    severity = "high"

    _SYSTEM_PATTERNS = [
        # System prompt markers
        r'(?:system\s*prompt|system\s*message|system\s*instructions?)\s*(?:is|are|contains?|says?|reads?)?\s*[:\-]',
        # Docker internals
        r'docker\s+(?:exec|run|compose|logs)\s+',
        r'container[_\s]?(?:id|name)[\s:]+\w+',
        # Internal configuration references
        r'settings\.json\s*(?:contains?|has|includes?)',
        r'\.env\s+file\s+(?:contains?|has)',
        # Agent framework internals
        r'AgentContext\s*\(',
        r'call_extensions\s*\(',
        r'loop_data\.\w+',
        r'Extension\s*\.\s*execute',
    ]

    def scan(self, text: str) -> tuple[str, List[Violation]]:
        violations = []
        filtered = text

        for pattern in self._SYSTEM_PATTERNS:
            for match in re.finditer(pattern, filtered, re.IGNORECASE):
                violations.append(Violation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=f"System/framework information leak: {match.group(0)[:40]}",
                    matched_text=match.group(0)[:50]
                ))
                filtered = filtered.replace(match.group(0), "[SYSTEM_INFO_REDACTED]")

        return filtered, violations


class HarmfulCommandDetector(FilterRule):
    """Detects harmful shell commands that could damage the system."""

    name = "HarmfulCommandDetector"
    severity = "critical"

    _DANGEROUS_PATTERNS = [
        # Destructive commands
        r'rm\s+(-rf?|--recursive)\s+/',
        r'mkfs\.',
        r'dd\s+if=.*of=/dev/',
        # Privilege escalation
        r'chmod\s+[0-7]*777',
        r'chmod\s+[ugo]*\+s',
        # RCA-248: chmod -R with non-executable modes breaks directory traversal
        # Owner digit 0/2/4/6 = no execute bit → directories become inaccessible
        r'chmod\s+(?:-[a-zA-Z]*[Rr][a-zA-Z]*|--recursive)\s+[0246][0-7]{2}\b',
        r'sudo\s+su\b',
        # Data exfiltration commands
        r'curl\s+.*-d\s+@',
        r'wget\s+.*--post-file',
        r'nc\s+-[a-z]*l',  # netcat listener
        r'scp\s+.*@.*:',
        # Crypto mining / malware indicators
        r'xmrig|cgminer|minerd',
        r'/dev/tcp/',
        r'base64\s+-d\s*\|.*sh',
    ]

    def scan(self, text: str) -> tuple[str, List[Violation]]:
        violations = []
        filtered = text

        for pattern in self._DANGEROUS_PATTERNS:
            for match in re.finditer(pattern, filtered, re.IGNORECASE):
                violations.append(Violation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=f"Harmful command detected: {match.group(0)[:40]}",
                    matched_text=match.group(0)[:50]
                ))
                filtered = filtered.replace(match.group(0), "[HARMFUL_COMMAND_REDACTED]")

        return filtered, violations


class CodeExfiltrationDetector(FilterRule):
    """Detects large dumps of internal source code or configuration."""

    name = "CodeExfiltrationDetector"
    severity = "high"

    # Markers that indicate system source code vs user code
    _SOURCE_MARKERS = [
        r'class\s+\w+Extension\s*\(',
        r'class\s+Api\w+\s*\(ApiHandler\)',
        r'from\s+python\.helpers\s+import',
        r'from\s+python\.extensions\s+import',
        r'def\s+message_loop_start\s*\(',
        r'def\s+monologue_end\s*\(',
        r'def\s+tool_execute_before\s*\(',
    ]

    def scan(self, text: str) -> tuple[str, List[Violation]]:
        violations = []
        filtered = text

        marker_count = 0
        for pattern in self._SOURCE_MARKERS:
            if re.search(pattern, filtered):
                marker_count += 1

        # If 2+ internal code markers found, flag as exfiltration
        if marker_count >= 2:
            violations.append(Violation(
                rule_name=self.name,
                severity=self.severity,
                description=f"Internal source code exfiltration detected ({marker_count} markers)",
                matched_text="[multiple internal code patterns]"
            ))
            # Redact code blocks that contain internal markers
            for pattern in self._SOURCE_MARKERS:
                for match in re.finditer(pattern, filtered):
                    # Find the enclosing code block or line
                    line_start = filtered.rfind('\n', 0, match.start()) + 1
                    line_end = filtered.find('\n', match.end())
                    if line_end == -1:
                        line_end = len(filtered)
                    original_line = filtered[line_start:line_end]
                    filtered = filtered.replace(original_line, "[INTERNAL_CODE_REDACTED]")

        return filtered, violations


class ArchitectureInfoDetector(FilterRule):
    """Detects architecture/framework details that reveal internal system design.
    
    Catches terms like lifecycle hooks, extension system, agent modes,
    Memory Bank structure, and framework-specific terminology that the LLM
    may reveal when asked conversationally about how the system works.
    """

    name = "ArchitectureInfoDetector"
    severity = "high"

    # Internal lifecycle hook names
    _HOOK_PATTERNS = [
        r'\bmessage_loop_start\b',
        r'\bmessage_loop_prompts_after\b',
        r'\bmonologue_start\b',
        r'\bmonologue_end\b',
        r'\btool_execute_before\b',
        r'\btool_execute_after\b',
        r'\bagent_init\b',
    ]

    # Framework-specific class/module names
    _FRAMEWORK_PATTERNS = [
        r'\bAgentContext\b',
        r'\bcall_extensions\b',
        r'\bloop_data\b',
        r'\bInterventionException\b',
        r'\bGuardrailsExtension\b',
        r'\bIntegrationOutputFilter\b',
        r'\bContentFilter\.scan\b',
        r'\bFilterRule\b',
        r'\bApiHandler\b',
        r'\bExtension\s*\.\s*execute\b',
        r'\bclass\s+\w+Extension\b',
        r'from\s+python\.helpers\b',
        r'from\s+python\.extensions\b',
        r'from\s+python\.tools\b',
        r'(?:^|\s|`)python/extensions/',
        r'(?:^|\s|`)python/helpers/',
        r'(?:^|\s|`)python/tools/',
        r'(?:^|\s|`)python/api/',
    ]

    # Architecture description patterns (concepts + technical terms together)
    _ARCHITECTURE_PATTERNS = [
        # Extension/plugin system descriptions
        r'(?:lifecycle|extension)\s+hooks?\b',
        r'\bextension\s+(?:system|point|mechanism)\b',
        r'\bplugin\s+system\b.*(?:python|extension|hook)',
        # Internal mode names (only when combined with internal details)
        r'(?:Supervisor|Subordinate)\s*[-–—]\s*(?:model|pattern|architecture)',
        # Memory Bank internal structure
        r'(?:^|\s|`)memory[_\s-]bank/',
        r'\bactiveContext\.md\b',
        r'\bsystemPatterns\.md\b',
        r'\btechContext\.md\b',
        r'\bprogress\.md\b',
        # Tool registry internals
        r'\btool\s+registry\b.*(?:pattern|class|inherit)',
        r'\bregistry\.json\b',
        # Internal paths mentioned as architecture
        r'/agix/usr/public/',
        r'/opt/venv-agix/',
        r'/opt/venv-agix/',
    ]

    def scan(self, text: str) -> tuple[str, List[Violation]]:
        violations = []
        filtered = text

        all_patterns = self._HOOK_PATTERNS + self._FRAMEWORK_PATTERNS + self._ARCHITECTURE_PATTERNS
        
        for pattern in all_patterns:
            for match in re.finditer(pattern, filtered, re.IGNORECASE):
                violations.append(Violation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=f"Architecture/framework detail leaked: {match.group(0)[:40]}",
                    matched_text=match.group(0)[:50]
                ))
                filtered = filtered.replace(match.group(0), "[INTERNAL_DETAIL_REDACTED]")

        return filtered, violations


# ---------------------------------------------------------------------------
# Content Filter Registry
# ---------------------------------------------------------------------------

class ContentFilter:
    """
    Central content filter with modular rules.
    
    Usage:
        result = ContentFilter.scan(response_text)
        if result.blocked:
            return "Response blocked for security reasons"
        return result.filtered
    """

    _rules: List[FilterRule] = []
    _initialized: bool = False

    @classmethod
    def _ensure_initialized(cls):
        """Lazy-init default rules on first use."""
        if cls._initialized:
            return
        cls._rules = [
            SecretDetector(),
            PathDetector(),
            SystemInfoDetector(),
            HarmfulCommandDetector(),
            CodeExfiltrationDetector(),
            ArchitectureInfoDetector(),
        ]
        cls._initialized = True

    @classmethod
    def register_rule(cls, rule: FilterRule):
        """Register an additional filter rule."""
        cls._ensure_initialized()
        cls._rules.append(rule)
        logger.info(f"[ContentFilter] Registered rule: {rule.name}")

    @classmethod
    def scan(cls, text: str) -> FilterResult:
        """
        Scan text through all registered rules.
        Returns FilterResult with filtered text and violations.
        """
        cls._ensure_initialized()

        if not text:
            return FilterResult(original="", filtered="")

        all_violations: List[Violation] = []
        filtered = text

        for rule in cls._rules:
            try:
                filtered, violations = rule.scan(filtered)
                all_violations.extend(violations)
            except Exception as e:
                logger.error(f"[ContentFilter] Rule {rule.name} failed: {e}")
                # Rule failure should not block the response
                continue

        # Determine if response should be fully blocked
        critical_count = sum(1 for v in all_violations if v.severity == "critical")
        blocked = critical_count >= 3  # Block if 3+ critical violations

        result = FilterResult(
            original=text,
            filtered=filtered,
            violations=all_violations,
            blocked=blocked
        )

        if all_violations:
            logger.warning(
                f"[ContentFilter] {len(all_violations)} violations found "
                f"({critical_count} critical). Blocked={blocked}"
            )
            for v in all_violations:
                logger.info(f"  [{v.severity}] {v.rule_name}: {v.description}")

        return result

    @classmethod
    def reset(cls):
        """Reset to default rules. Useful for testing."""
        cls._rules = []
        cls._initialized = False
