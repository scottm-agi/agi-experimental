"""
BuildLoopDetector — Circuit breaker for build-fix death spirals.
================================================================

Tracks consecutive build failures per project directory. After N failures
(configurable threshold, default 3), returns a structured diagnostic message
that tells the agent to STOP fixing incrementally and instead reconcile
types/schema alignment.

Escalation tiers (ITR-35 RC-4):
    - Tier 1 (>=threshold): Inject diagnostic message (basic intervention)
    - Tier 2 (>=5 failures): Add ESCALATION_REQUIRED flag for supervisor
    - Tier 3 (>=8 failures): Add ESCALATION_REQUIRED + HARD_BLOCK flags

Usage:
    detector = BuildLoopDetector(threshold=5)
    
    # On build failure:
    diagnostic = detector.record_failure(project_dir, error_output)
    if diagnostic:
        # Inject diagnostic into agent context instead of allowing retry
        return diagnostic
    
    # On build success:
    detector.record_success(project_dir)

Integration point:
    Wire into code_execution_tool after_execution — if the command was
    `npm run build` and exit code != 0, call record_failure().

Root cause (ADR-018):
    Iteration 1777062732: Agent entered 95-iteration loop of
    "fix TypeScript error → new error → fix that" because no circuit
    breaker existed. This class prevents that pattern.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Optional, List

from python.helpers.output_truncation import truncate_output_middle_out
from python.helpers.error_line_extractor import extract_error_lines
from python.helpers import build_error_paginator

logger = logging.getLogger("agix.build_loop_detector")

# v2: String patterns that indicate a build failure in stdout/stderr,
# even when exit codes are masked by log redirection (e.g., npm run build 2>&1)
FAILURE_PATTERNS = [
    re.compile(r"Build error occurred", re.IGNORECASE),
    re.compile(r"Build failed", re.IGNORECASE),
    re.compile(r"Failed to compile", re.IGNORECASE),
    re.compile(r"error TS\d{4,}:", re.IGNORECASE),
    re.compile(r"Module not found", re.IGNORECASE),
    re.compile(r"Cannot find module", re.IGNORECASE),
    re.compile(r"SyntaxError:", re.IGNORECASE),
    re.compile(r"TypeError:", re.IGNORECASE),
    # RC-1: Test runner failure patterns
    re.compile(r"FAIL\s+\S+\.test\.", re.IGNORECASE),
    re.compile(r"Tests:\s+\d+\s+failed", re.IGNORECASE),
    re.compile(r"Test Suites:\s+\d+\s+failed", re.IGNORECASE),
]


class BuildLoopDetector:
    """Tracks consecutive build failures per project and triggers diagnostics."""

    # ITR-35 RC-4: Tiered escalation thresholds.
    # Tier 1 fires at self.threshold (default 2-3).
    # Tier 2 and Tier 3 MUST be strictly greater than threshold to keep tiers distinct.
    TIER_2_ESCALATION = 5   # Supervisor intervention required
    TIER_3_HARD_BLOCK = 8   # Agent must stop all build attempts

    def __init__(self, threshold: int = 2, advisor=None):
        """
        Args:
            threshold: Number of consecutive failures before injecting diagnostic.
            advisor: Optional advisor object with a format_hint(error_text, key) method.
                When provided, its output (if non-None) overrides the default diagnostic.
                Backward-compatible parameter — production code doesn't require an advisor.
        """
        self.threshold = threshold
        self._advisor = advisor
        self._failure_counts: dict[str, int] = defaultdict(int)
        self._error_history: dict[str, List[str]] = defaultdict(list)
        self._full_error_history: dict[str, List[str]] = defaultdict(list)
        # A5/RCA-475: Track which requirement_ids are associated with build failures
        # so the escape hatch can mark the specific requirements as partial/failed.
        self._looped_req_ids: dict[str, set] = defaultdict(set)

    def record_failure(
        self,
        project_dir: str,
        error_output: str,
        *,
        requirement_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Record a build failure. Returns diagnostic if loop detected, None otherwise.

        Args:
            project_dir: Absolute path to the project directory.
            error_output: The stderr/stdout from the failed build command.
            requirement_ids: Optional list of requirement IDs associated with
                this build attempt. Accumulated across failures so the escape
                hatch can identify WHICH requirements caused the loop.
                (A5/RCA-475)

        Returns:
            A structured diagnostic message if the failure count has reached
            the threshold, or None if the agent should continue self-correcting.
        """
        key = self._normalize_key(project_dir)
        self._failure_counts[key] += 1
        # Store middle-out truncated version for diagnostics
        self._error_history[key].append(
            truncate_output_middle_out(error_output, max_chars=2000, head_ratio=0.3)
        )
        # Try to read the true raw output saved by truncation system
        raw = self._read_raw_output_from_disk()
        full_output = raw if raw else error_output
        self._full_error_history[key].append(full_output)

        # A5/RCA-475: Accumulate requirement_ids across failures
        if requirement_ids:
            self._looped_req_ids[key].update(requirement_ids)

        # Wire: store in paginator for agent self-service retrieval
        build_error_paginator.store_error(project_dir, full_output)

        count = self._failure_counts[key]

        # RCA-475 D2: SM wrap — track tier transitions alongside counter
        self._sync_build_loop_sm(project_dir)

        if count >= self.threshold:
            diagnostic = self._generate_diagnostic(key, count)
            logger.warning(
                f"BuildLoopDetector: {count} consecutive failures in {project_dir}. "
                f"Injecting diagnostic."
            )
            return diagnostic

        return None

    def record_success(self, project_dir: str) -> None:
        """Reset failure counter on successful build."""
        key = self._normalize_key(project_dir)
        if key in self._failure_counts:
            prev_count = self._failure_counts[key]
            if prev_count > 0:
                logger.info(
                    f"BuildLoopDetector: Build succeeded after {prev_count} failures "
                    f"in {project_dir}. Counter reset."
                )
            del self._failure_counts[key]
        if key in self._error_history:
            del self._error_history[key]
        if key in self._full_error_history:
            del self._full_error_history[key]
        # A5/RCA-475: Clear accumulated requirement_ids on success
        if key in self._looped_req_ids:
            del self._looped_req_ids[key]
        # Wire: clear paginator on success
        build_error_paginator.clear_errors(project_dir)

        # RCA-475 D2: SM wrap — reset to 'ok' on success
        self._sync_build_loop_sm(project_dir)

    def get_failure_count(self, project_dir: str) -> int:
        """Get current failure count for a project."""
        return self._failure_counts.get(self._normalize_key(project_dir), 0)

    def get_looped_requirement_ids(self, project_dir: str) -> set:
        """Get all requirement_ids accumulated during build failures.

        A5/RCA-475: Returns the set of requirement IDs associated with
        build failures for this project. Enables the escape hatch to
        mark specific requirements as partial/failed instead of all.

        Args:
            project_dir: Absolute path to the project directory.

        Returns:
            Set of requirement ID strings. Empty set if no failures recorded.
        """
        key = self._normalize_key(project_dir)
        return set(self._looped_req_ids.get(key, set()))

    def get_escalation_tier(self, project_dir: str) -> int:
        """Get the current escalation tier for a project.

        Returns:
            0 = no intervention (below threshold)
            1 = diagnostic injected (>= threshold)
            2 = ESCALATION_REQUIRED (>= TIER_2_ESCALATION, supervisor should intervene)
            3 = HARD_BLOCK (>= TIER_3_HARD_BLOCK, agent must stop all builds)
        """
        count = self.get_failure_count(project_dir)
        if count >= self.TIER_3_HARD_BLOCK:
            return 3
        elif count >= self.TIER_2_ESCALATION:
            return 2
        elif count >= self.threshold:
            return 1
        return 0

    def is_in_loop(self, project_dir: str) -> bool:
        """Check if a project is currently in a detected build loop."""
        return self.get_failure_count(project_dir) >= self.threshold

    # ── RCA-475 D2: BuildLoopSM wrapper ──────────────────────────────

    _TIER_TO_SM_STATUS = {
        0: "ok",
        1: "tier1_warn",
        2: "tier2_escalate",
        3: "tier3_hard_block",
    }

    def _get_or_create_build_loop_sm(self):
        """Get or create the BuildLoopSM stored on this detector.

        RCA-479 Fix: Handles corrupted SM from JSON round-trip.
        """
        from python.helpers.state_machines.build_loop_sm import BuildLoopSM
        existing = getattr(self, "_build_loop_sm", None)
        if not isinstance(existing, BuildLoopSM):
            self._build_loop_sm = BuildLoopSM(entity_id="build_loop")
        return self._build_loop_sm

    def _sync_build_loop_sm(self, project_dir: str) -> None:
        """Sync BuildLoopSM state with the highest escalation tier.

        Computes the max tier across all tracked projects and transitions
        the SM to the corresponding state. Invalid transitions are
        force-synced with a warning (migration mode — warn, not block).
        """
        sm = self._get_or_create_build_loop_sm()

        # Compute highest tier across all projects
        max_tier = 0
        for key in self._failure_counts:
            count = self._failure_counts[key]
            if count >= self.TIER_3_HARD_BLOCK:
                max_tier = max(max_tier, 3)
            elif count >= self.TIER_2_ESCALATION:
                max_tier = max(max_tier, 2)
            elif count >= self.threshold:
                max_tier = max(max_tier, 1)

        target = self._TIER_TO_SM_STATUS.get(max_tier, "ok")

        if sm.status == target:
            return  # already in correct state

        ok, msg = sm.transition(
            target,
            reason=f"tier sync: {project_dir} (max_tier={max_tier})",
            source="build_loop_detector._sync_build_loop_sm",
        )
        if not ok:
            logger.warning(f"[BUILD_LOOP SM] {msg} — force-syncing (migration mode)")
            sm.transition(
                target,
                reason=f"force-sync: {msg}",
                source="build_loop_detector._sync_build_loop_sm",
                force=True,
            )

    def _normalize_key(self, project_dir: str) -> str:
        """Normalize path for consistent tracking."""
        return project_dir.rstrip("/")

    def _read_raw_output_from_disk(self) -> Optional[str]:
        """Read the true raw output from /tmp/last_cmd_output.log.

        The truncation system saves the full command output to this file
        before truncating it for the agent context. This method retrieves
        the un-truncated version.

        Returns:
            The full raw output string, or None if the file doesn't exist
            or can't be read.
        """
        try:
            with open("/tmp/last_cmd_output.log", "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except (FileNotFoundError, PermissionError, OSError):
            return None


    def _build_tier_prefix(self, count: int) -> str:
        """Build escalation tier prefix flags for diagnostic messages.

        ITR-35 RC-4: Higher failure counts produce stronger signals:
            - Tier 1 (>= threshold, < TIER_2_ESCALATION): No prefix (diagnostic only)
            - Tier 2 (>= TIER_2_ESCALATION, < TIER_3_HARD_BLOCK): ESCALATION_REQUIRED flag
            - Tier 3 (>= TIER_3_HARD_BLOCK): ESCALATION_REQUIRED + HARD_BLOCK flags

        Returns:
            String prefix to prepend to diagnostic messages.
        """
        if count >= self.TIER_3_HARD_BLOCK:
            return (
                "<!-- ESCALATION_REQUIRED -->\n"
                "<!-- HARD_BLOCK -->\n"
                f"## 🚨 HARD BLOCK — {count} CONSECUTIVE BUILD FAILURES\n"
                f"\n"
                f"**YOU MUST STOP ALL BUILD ATTEMPTS IMMEDIATELY.** "
                f"This project has failed {count} times in a row. "
                f"Continuing to retry is wasting resources. "
                f"Escalate to supervisor for architectural review.\n"
                f"\n"
            )
        elif count >= self.TIER_2_ESCALATION:
            return (
                "<!-- ESCALATION_REQUIRED -->\n"
                f"## ⚠️ ESCALATION REQUIRED — {count} CONSECUTIVE BUILD FAILURES\n"
                f"\n"
                f"**Supervisor intervention is required.** "
                f"This project has failed {count} times in a row. "
                f"The build loop diagnostic alone is not sufficient — "
                f"escalate for architectural review before attempting any more fixes.\n"
                f"\n"
            )
        return ""

    def _universal_context_gather_preamble(self, count: int, domain: str) -> str:
        """Universal context-gathering preamble for all diagnostics.

        RCA-465/466: Instead of bespoke per-framework fixes, this teaches
        the agent HOW to recover from errors universally:
        - Count 1+: Read related config files (the agent can figure it out)
        - Count 2+: Use search tools (docs_lookup, search_engine) to research
        - Count 3+: Explain WHY the error occurs before attempting another fix

        This is the Reflexion protocol (Shinn et al. 2023) — the missing
        step between "error occurred" and "attempt fix".
        """
        lines = []

        # Always: read config files relevant to the error domain
        lines.append(
            "### ⚡ BEFORE attempting ANY fix, gather context:\n"
            "\n"
        )

        # Domain-aware config file suggestions
        config_files = {
            "typescript": [
                "`tsconfig.json`", "`package.json`",
                "test config (`vitest.config.ts` / `jest.config.ts`)"
            ],
            "css": [
                "`tailwind.config.ts`", "`postcss.config.mjs`",
                "`globals.css`"
            ],
            "module": [
                "`package.json`", "`tsconfig.json` paths/aliases"
            ],
            "infra": [
                "`next.config.js`", "`package.json`",
                "`.env` / `.env.local`"
            ],
            "eslint": [
                "`.eslintrc.*`", "`tsconfig.json`"
            ],
        }

        files_to_read = config_files.get(domain, config_files["typescript"])
        file_list = ", ".join(files_to_read)
        lines.append(
            f"1. **Read the relevant configuration files**: {file_list}\n"
        )

        # Count >= 2: use search tools
        if count >= 2:
            lines.append(
                "2. **Use `docs_lookup` or `search_engine`** to research "
                "this error message + your framework version. The answer "
                "exists — find it before guessing.\n"
            )

        # Count >= 3: explain WHY
        if count >= 3:
            lines.append(
                "3. **Explain WHY this error occurs** (write your root cause "
                "analysis in a comment) before proposing a fix. If you can't "
                "explain the reason, you don't understand the error yet.\n"
            )

        lines.append("\n")
        return "".join(lines)

    def _generate_diagnostic(self, key: str, count: int) -> str:
        """Generate a structured diagnostic message for the agent.

        If an advisor is available, queries it for known error patterns
        and includes targeted fix advice. Falls back to domain-aware
        generic advice when no specific match is found.

        ITR-35 RC-4: Prepends escalation tier flags based on failure count:
            - Tier 2 (>=7): <!-- ESCALATION_REQUIRED -->
            - Tier 3 (>=12): <!-- ESCALATION_REQUIRED --> <!-- HARD_BLOCK -->

        Domain classification (Iteration 213):
            - CSS/Tailwind errors → Tailwind config / globals.css advice
            - Module not found → npm install / dependency advice
            - TypeScript errors → type reconciliation advice (original behavior)

        RCA-465: Prepends universal context-gathering preamble (Reflexion
        protocol) to all diagnostics — read config, search, reflect.
        """
        errors = self._error_history.get(key, [])

        # ── ITR-35 RC-4: Compute escalation tier prefix ──
        tier_prefix = self._build_tier_prefix(count)


        error_patterns = self._extract_patterns(errors)
        pattern_summary = ""
        if error_patterns:
            pattern_lines = [f"  - {p}" for p in error_patterns[:5]]
            pattern_summary = (
                "\n### Repeated Error Patterns\n"
                + "\n".join(pattern_lines)
                + "\n"
            )

        # Classify the error domain from all recent errors
        combined_errors = "\n".join(errors[-3:]) if errors else ""
        domain = self._classify_error_domain(combined_errors)

        # ── RCA-465: Universal context-gathering preamble ──
        context_preamble = self._universal_context_gather_preamble(count, domain)

        if domain == "css":
            base = self._generic_css_diagnostic(count, pattern_summary)
        elif domain == "module":
            base = self._generic_module_diagnostic(count, pattern_summary)
        elif domain == "eslint":
            base = self._generic_eslint_diagnostic(count, pattern_summary)
        elif domain == "infra":
            base = self._generic_infra_diagnostic(count, pattern_summary)
        else:
            base = self._generic_typescript_diagnostic(count, pattern_summary)

        # ── Advisor integration (backward-compat) ──
        # If an advisor was provided, try to get a targeted fix hint.
        # When advisor returns a match, use it instead of the domain base.
        advisor_hint = None
        if self._advisor is not None:
            # Use smart-extracted error text when available
            full_errors_adv = self._full_error_history.get(key, [])
            if full_errors_adv:
                from python.helpers.error_line_extractor import extract_error_lines
                try:
                    extracted = extract_error_lines(full_errors_adv[-1])
                    advisor_text = extracted if extracted.strip() else errors[-1] if errors else ""
                except Exception:
                    advisor_text = errors[-1] if errors else ""
            else:
                advisor_text = errors[-1] if errors else ""
            try:
                advisor_hint = self._advisor.format_hint(advisor_text, key)
            except Exception:
                advisor_hint = None

        if advisor_hint is not None:
            return f"{tier_prefix}{context_preamble}{advisor_hint}"

        # ── Self-service directive for large unmatched errors ──
        full_errors = self._full_error_history.get(key, [])
        if full_errors and len(full_errors[-1]) > 5000:
            self_service_hint = (
                "\n### 📋 FULL ERROR OUTPUT AVAILABLE\n"
                "The build output was too large for automatic pattern matching. "
                "To find the actual error:\n"
                "1. `grep -n 'Error\\|error:\\|TypeError\\|Cannot\\|FAIL' /tmp/last_cmd_output.log | head -20`\n"
                "2. `tail -100 /tmp/last_cmd_output.log` (errors often at the end)\n"
                "3. `cat /tmp/last_cmd_output.log | grep -A3 'prerendering\\|useContext'`\n"
                "4. Or use `code_execution_tool` with `runtime='full_output'` to retrieve it\n"
            )
            base += self_service_hint

        return f"{tier_prefix}{context_preamble}{base}"

    def _classify_error_domain(self, error_text: str) -> str:
        """Classify error text into a domain for fallback advice routing.

        Returns: 'infra', 'css', 'module', 'eslint', or 'typescript' (default).
        """
        if not error_text:
            return "typescript"

        text = error_text.lower()

        # RCA-ITR34-BL1 Fix 4: Prerender errors are CODE bugs, not infra.
        # These MUST be checked BEFORE infra_keywords because they contain
        # overlapping terms ("use client", "useContext") that would otherwise
        # misclassify to infra domain and prescribe cache-clearing.
        prerender_code_keywords = [
            "error occurred prerendering page",
            "prerender error", "prerender-error",
            "export encountered errors",
            "cannot read properties of null",
            "generatestaticparams",
        ]
        for kw in prerender_code_keywords:
            if kw in text:
                return "typescript"  # Route to code fix, not infra/cache-clear

        # Infra/config domain (F-4: must be checked BEFORE module/typescript)
        # These are Next.js/React infrastructure issues that need cache clearing
        # and dev server restart, NOT code edits.
        infra_keywords = [
            "html import error", "use client", "use server",
            "server component", "client component",
            "next/font", "next/image", "next/link",
            "swc", ".next/cache", ".next/server",
            "importing a component that needs usestate",
            "importing a component that needs useeffect",
            "unhandled runtime error", "hydration failed",
            "hydration mismatch", "react server component",
            "server actions",
        ]
        for kw in infra_keywords:
            if kw in text:
                return "infra"

        # CSS/Tailwind domain
        css_keywords = [
            "tailwind", "postcss", "csssyntaxerror", "css property",
            "border-border", "bg-background", "text-foreground",
            "globals.css", "unknown word", "theme", "css variable",
            "@tailwind", "@apply", "autoprefixer",
        ]
        for kw in css_keywords:
            if kw in text:
                return "css"

        # ESLint domain
        eslint_keywords = [
            "no-unused-vars", "@typescript-eslint/no-unused-vars",
            "eslint", "is defined but never used",
            "is assigned a value but never used",
            "react/no-unescaped-entities", "react-hooks/",
        ]
        for kw in eslint_keywords:
            if kw in text:
                return "eslint"

        # Module resolution domain
        module_keywords = [
            "module not found", "cannot find module", "can't resolve",
            "cannot resolve", "npm err", "enoent", "package.json",
        ]
        for kw in module_keywords:
            if kw in text:
                return "module"

        # Default: TypeScript / general
        return "typescript"

    def _generic_css_diagnostic(self, count: int, pattern_summary: str) -> str:
        """Domain-specific fallback for CSS/Tailwind errors."""
        return (
            f"## 🛑 BUILD LOOP DETECTED ({count} consecutive failures)\n"
            f"\n"
            f"You have failed the build {count} times in a row. "
            f"**STOP fixing errors incrementally.** The errors are CSS/Tailwind-related.\n"
            f"\n"
            f"### Required Actions (in order)\n"
            f"\n"
            f"1. **Read `tailwind.config.ts`** (or .js) completely\n"
            f"2. **Read `src/app/globals.css`** (or equivalent CSS entry point)\n"
            f"3. **Read `postcss.config.mjs`** (or .js/.cjs)\n"
            f"4. **Check if using CSS variable theming** (shadcn/ui style) — if so, "
            f"ensure ALL color tokens (border, background, foreground, primary, etc.) "
            f"are mapped in tailwind.config.ts `theme.extend.colors`\n"
            f"5. **Verify PostCSS config** has tailwindcss and autoprefixer plugins\n"
            f"6. **Delete conflicting configs** — if both .js and .ts/.mjs exist, remove .js\n"
            f"7. **Only then** re-run `npm run build`\n"
            f"{pattern_summary}"
            f"\n"
            f"### Why This Happened\n"
            f"CSS/Tailwind configuration is inconsistent — either theme colors are "
            f"not defined, PostCSS is misconfigured, or config file conflicts exist. "
            f"Fix the configuration files FIRST, not individual component styles.\n"
        )

    def _generic_infra_diagnostic(self, count: int, pattern_summary: str) -> str:
        """Domain-specific fallback for infrastructure/config errors (F-4).

        Covers Next.js server components, 'use client' directives, HTML imports,
        next/font issues, hydration mismatches, and stale .next cache problems.
        The key advice is: clear caches, restart dev server, don't edit source files.
        """
        # RCA-ITR34-BL1 Fix 5: Removed destructive `rm -rf .next` from
        # infra diagnostic. The previous guidance told agents to nuke the
        # entire .next build directory, destroying working builds and causing
        # rebuild death spirals. Now guides agents to READ the error first
        # and only clear .next/cache (not the full directory) if the error
        # specifically references stale cache files.
        return (
            f"## 🛑 BUILD LOOP DETECTED ({count} consecutive failures)\n"
            f"\n"
            f"You have failed the build {count} times in a row. "
            f"The errors may be infrastructure/config-related.\n"
            f"\n"
            f"### Required Actions (in order)\n"
            f"\n"
            f"1. **Read the FULL build error output** carefully — identify the ACTUAL error\n"
            f"2. **If error says 'prerendering page' or 'useContext'**: This is a CODE bug — "
            f"add `export const dynamic = 'force-dynamic'` or `'use client'` directive\n"
            f"3. **If error says '<Html> should not be imported outside of pages/_document'**: "
            f"Remove `next/document` imports and use App Router patterns (`not-found.tsx`, `error.tsx`)\n"
            f"4. **If error specifically references stale .next/cache files**: "
            f"Clear ONLY the cache: `rm -rf .next/cache` (NOT the entire .next directory)\n"
            f"5. **Run `npm install`** to ensure dependencies are clean\n"
            f"6. **Restart the dev server** via `services_mgt` tool\n"
            f"7. **Common infra fixes**:\n"
            f"   - Server component import errors → ensure server components don't import client-only code\n"
            f"   - next/font errors → check that SWC is enabled in next.config\n"
            f"   - HTML import errors → verify layout.tsx imports are correct\n"
            f"8. **Only then** re-run `npm run build`\n"
            f"{pattern_summary}"
            f"\n"
            f"### Why This Happened\n"
            f"The build is failing due to a specific error that needs targeted fixing. "
            f"Read the error output above to identify whether it's a code bug, "
            f"a configuration issue, or a genuine cache problem.\n"
        )

    def _generic_module_diagnostic(self, count: int, pattern_summary: str) -> str:
        """Domain-specific fallback for module resolution errors."""
        return (
            f"## 🛑 BUILD LOOP DETECTED ({count} consecutive failures)\n"
            f"\n"
            f"You have failed the build {count} times in a row. "
            f"**STOP fixing errors incrementally.** The errors are missing-module-related.\n"
            f"\n"
            f"### Required Actions (in order)\n"
            f"\n"
            f"1. **Read `package.json`** completely — check ALL dependencies\n"
            f"2. **Run `npm install`** to ensure all deps are resolved\n"
            f"3. **Check for missing packages** — each `Module not found` error "
            f"indicates a package that needs `npm install <package-name>`\n"
            f"4. **Verify import paths** — ensure `@/` aliases are configured in "
            f"tsconfig.json under `paths`\n"
            f"5. **Only then** re-run `npm run build`\n"
            f"{pattern_summary}"
            f"\n"
            f"### Why This Happened\n"
            f"Dependencies are missing or import paths are incorrect. Install all "
            f"required packages and verify path aliases before retrying the build.\n"
        )

    def _generic_eslint_diagnostic(self, count: int, pattern_summary: str) -> str:
        """Domain-specific fallback for ESLint errors."""
        return (
            f"## 🛑 BUILD LOOP DETECTED ({count} consecutive failures)\n"
            f"\n"
            f"You have failed the build {count} times in a row. "
            f"**STOP fixing errors incrementally.** The errors are ESLint/lint-related.\n"
            f"\n"
            f"### 🔴 CRITICAL: Do NOT use `write_to_file` to fix lint errors\n"
            f"Use `replace_in_file` to SURGICALLY remove/fix the offending lines. "
            f"Rewriting entire files with `write_to_file` will be blocked and creates loops.\n"
            f"\n"
            f"### Required Actions (in order)\n"
            f"\n"
            f"1. **Check `next.config.mjs`** — verify it has `eslint: {{ ignoreDuringBuilds: true }}` "
            f"and `typescript: {{ ignoreBuildErrors: true }}`. If missing, add with `replace_in_file`.\n"
            f"2. **For `no-unused-vars`** — use `replace_in_file` to DELETE the unused import line entirely. "
            f"For unused function params, prefix with `_` (e.g., `_req: Request`).\n"
            f"3. **For `no-explicit-any`** — replace `any` with the proper type or `unknown`.\n"
            f"4. **For auto-fixable issues** — run `cd web && npx eslint --fix src/`\n"
            f"5. **Only then** re-run `npm run build`\n"
            f"{pattern_summary}"
            f"\n"
            f"### Why This Happened\n"
            f"Code was written with unused imports/variables. The fix is surgical removal "
            f"with `replace_in_file`, NOT file rewrites with `write_to_file`.\n"
        )

    def _generic_typescript_diagnostic(self, count: int, pattern_summary: str) -> str:
        """Domain-specific fallback for TypeScript type errors (original behavior)."""
        return (
            f"## 🛑 BUILD LOOP DETECTED ({count} consecutive failures)\n"
            f"\n"
            f"You have failed the build {count} times in a row. "
            f"**STOP fixing errors incrementally.** Incremental fixes are creating "
            f"new errors faster than they resolve old ones.\n"
            f"\n"
            f"### Required Actions (in order)\n"
            f"\n"
            f"1. **Read `src/types/index.ts`** (or equivalent types file) completely\n"
            f"2. **Read `prisma/schema.prisma`** (or equivalent schema)\n"
            f"3. **Reconcile ALL type definitions** — ensure every type imported "
            f"across the codebase is actually exported from the types file\n"
            f"4. **Grep for all imports from `@/types`** and verify each imported "
            f"symbol exists in the types file\n"
            f"5. **Fix the types file FIRST**, then fix all importers to match\n"
            f"6. **Only then** re-run `npm run build`\n"
            f"{pattern_summary}"
            f"\n"
            f"### Why This Happened\n"
            f"Multiple agents built different files in parallel with inconsistent "
            f"type naming (e.g., `Business` vs `Lead` vs `Prospect`). Each fix to "
            f"one file breaks another because the type names are fundamentally "
            f"inconsistent. The fix is to reconcile types at the source, not patch "
            f"individual files.\n"
        )

    def _extract_patterns(self, errors: List[str]) -> List[str]:
        """Extract commonly repeated error patterns from error history."""
        if not errors:
            return []

        # Simple frequency analysis of error fragments
        from collections import Counter
        
        fragments = []
        for error in errors:
            # Extract key error phrases
            for line in error.split("\n"):
                line = line.strip()
                if any(kw in line.lower() for kw in [
                    "module not found", "cannot find", "is not exported",
                    "type error", "property", "does not exist",
                    "cannot resolve", "not assignable",
                ]):
                    fragments.append(line[:120])

        counter = Counter(fragments)
        return [pattern for pattern, count in counter.most_common(5) if count >= 2]

    # ── v2: String-based failure detection (ADR-019, Iteration 151) ──

    def detect_failure_in_output(self, output: str) -> bool:
        """Check if build output text contains failure patterns.

        This bypasses exit code masking (e.g., npm run build 2>&1 always
        returns exit code 0). Scans stdout/stderr for known failure strings.

        Args:
            output: The stdout/stderr text from a build command.

        Returns:
            True if any failure pattern is found in the output.
        """
        if not output:
            return False
        return any(p.search(output) for p in FAILURE_PATTERNS)

    def record_failure_from_output(
        self, project_dir: str, output: str, exit_code: int = 0
    ) -> Optional[str]:
        """Combined detection: exit code OR string patterns in output.

        Use this instead of record_failure() when you have both exit code
        and output text. Handles the common case where exit codes are masked.

        Args:
            project_dir: Absolute path to the project directory.
            output: The stdout/stderr text from the build command.
            exit_code: The command's exit code (may be masked to 0).

        Returns:
            Diagnostic string if loop detected, None otherwise.
        """
        is_failure = exit_code != 0 or self.detect_failure_in_output(output)

        if is_failure:
            return self.record_failure(project_dir, output)
        else:
            self.record_success(project_dir)
            return None


# ──────────────────────────────────────────────────────────────────────
# Wiring Helpers (ITR-32 Fix 5, RC-B)
# Used by code_execution.py after_execution() to detect build commands
# and route output through the detector.
# ──────────────────────────────────────────────────────────────────────

BUILD_COMMAND_PATTERNS = [
    re.compile(r'\bnpm\s+run\s+build\b'),
    re.compile(r'\bnext\s+build\b'),
    re.compile(r'\bvite\s+build\b'),
    re.compile(r'\btsc\b'),
    re.compile(r'\bwebpack\b'),
    re.compile(r'\bturbo\s+build\b'),
    re.compile(r'\bnuxt\s+build\b'),
]


def is_build_command(command: Optional[str]) -> bool:
    """Check if a shell command is a build command.

    Used by code_execution.py to decide whether to route the output
    through BuildLoopDetector.

    Args:
        command: Shell command string (may contain env vars, &&, etc.)

    Returns:
        True if the command matches any BUILD_COMMAND_PATTERNS.
    """
    if not command:
        return False
    return any(p.search(command) for p in BUILD_COMMAND_PATTERNS)


def get_or_create_build_loop_detector(agent) -> BuildLoopDetector:
    """Get or create a BuildLoopDetector cached on agent.data.

    Creates a single detector per agent, cached under '_build_loop_detector'
    in agent.data. This ensures failure counts persist across tool calls
    within the same agent session.

    RCA-MSR-BuildLoop Fix 2: If _build_failure_seed exists in agent.data
    (set by seed_build_loop_detector), the new detector is pre-loaded with
    those failure counts so escalation tiers carry across delegation retries.

    Args:
        agent: The Agent instance (must have a .data dict).

    Returns:
        A BuildLoopDetector instance.
    """
    key = "_build_loop_detector"
    existing = agent.data.get(key)
    if isinstance(existing, BuildLoopDetector):
        return existing

    detector = BuildLoopDetector(threshold=3)

    # RCA-MSR-BuildLoop: Seed from propagated state if available
    seed = agent.data.get("_build_failure_seed")
    if isinstance(seed, dict):
        for project_dir, count in seed.items():
            if isinstance(count, int) and count > 0:
                # FIX-011: Cap seeded count at threshold-1 so new agents
                # get at least one attempt before Tier 1 fires.
                # Without this cap, max() merge monotonically ratchets
                # the counter — new agents are born at Tier 5.
                capped = min(count, detector.threshold - 1)
                detector._failure_counts[detector._normalize_key(project_dir)] = capped
                logger.info(
                    f"BuildLoopDetector: Seeded {capped} failures for "
                    f"{project_dir} (original={count}, "
                    f"threshold={detector.threshold})"
                )
        # Clear seed after consuming
        del agent.data["_build_failure_seed"]

    agent.data[key] = detector
    return detector


def get_propagatable_build_state(agent) -> dict:
    """Extract build failure counts from an agent for propagation.

    RCA-MSR-BuildLoop: When a subordinate finishes (success or failure),
    its build failure counts should be propagated back to the orchestrator
    so the next subordinate for the same project can inherit them.

    This breaks the "island state" pattern where each new subordinate
    starts with failure_count=0, making Tier 2 (7) and Tier 3 (12)
    architecturally unreachable across delegation retries.

    Args:
        agent: The Agent instance whose detector state to extract.

    Returns:
        Dict mapping project_dir → failure_count. Empty dict if no detector.
    """
    key = "_build_loop_detector"
    detector = agent.data.get(key)
    if not isinstance(detector, BuildLoopDetector):
        return {}

    return dict(detector._failure_counts)


def seed_build_loop_detector(agent, propagated_state: dict) -> None:
    """Pre-seed an agent's build failure state from a previous subordinate.

    RCA-MSR-BuildLoop: Called when creating a new subordinate that will
    work on the same project as a previous subordinate. The new subordinate's
    BuildLoopDetector will start with the inherited failure counts.

    Args:
        agent: The new Agent instance to seed.
        propagated_state: Dict mapping project_dir → failure_count.
    """
    if not propagated_state:
        return

    agent.data["_build_failure_seed"] = propagated_state
    logger.info(
        f"BuildLoopDetector: Stored seed state for new subordinate: "
        f"{propagated_state}"
    )
