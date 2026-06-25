from __future__ import annotations
import os
import re
from python.helpers.tool import Tool, Response
from python.helpers import files, projects
from python.helpers.file_guard import FileGuard
from python.helpers.read_before_write_guard import check_read_before_write, check_read_before_write_proactive, record_file_write
from python.helpers.mock_data_detector import detect_mock_data

class ReplaceInFile(Tool):
    async def execute(self, **kwargs) -> Response:
        path = self.args.get("path")

        if not path:
            return Response(message="Error: Missing 'path' argument.", break_loop=False)

        # ── Resolve replacement pairs from EITHER API format ──
        pairs, err = self._resolve_pairs()
        if err:
            return Response(message=err, break_loop=False)

        try:
            # ISS-4: Use canonical project-aware resolver for relative paths.
            # Previously used files.get_abs_path() which resolves to framework root.
            if not os.path.isabs(path):
                from python.helpers.resolve_agent_path import resolve_agent_path
                abs_path = resolve_agent_path(path, self.agent)
            else:
                abs_path = path

            if not os.path.exists(abs_path):
                return Response(message=f"Error: File '{path}' not found.", break_loop=False)

            # ── FileGuard: Enforce project scope ──
            active_project = projects.get_context_project_name(self.agent.context)
            is_allowed, guard_msg = FileGuard.validate_write_path(abs_path, active_project)
            if not is_allowed:
                return Response(
                    message=f"FileGuard: {guard_msg}",
                    break_loop=False
                )

            # ADR-012: Consume AUTO_RESOLVED path — FileGuard may correct
            # /agix/src/... → /agix/usr/projects/<name>/src/...
            if guard_msg.startswith("AUTO_RESOLVED:"):
                resolved_path = guard_msg.split("AUTO_RESOLVED:", 1)[1]
                abs_path = resolved_path
                path = resolved_path

            # ── Read-Before-Write Guard: Ensure agent read the file first ──
            agent_id = str(getattr(self.agent, 'number', 'unknown'))
            advisory_warnings = []

            # ADR-010: Try proactive guard first (auto-reads and advises)
            proactive_result = check_read_before_write_proactive(
                agent_id=agent_id,
                abs_path=abs_path,
            )
            if proactive_result:
                advisory_warnings.append(proactive_result.warning)
            else:
                # Fall back to blocking guard for edge cases
                rbw_msg = check_read_before_write(
                    agent_id=agent_id,
                    abs_path=abs_path,
                )
                if rbw_msg:
                    return Response(message=rbw_msg, break_loop=False)

            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Apply each replacement pair using 3-strategy cascade
            total_replaced = 0
            not_found = []
            for search_str, replace_str in pairs:
                # RCA-462: Check for structural damage risk before applying
                structural_risk = _detect_structural_risk(search_str, replace_str, content)
                if structural_risk:
                    advisory_warnings.append(structural_risk)

                new_content, count, strategy = _apply_replacement(content, search_str, replace_str)
                if count == 0:
                    not_found.append(search_str[:60])
                    continue
                content = new_content
                total_replaced += count
                if strategy != "exact":
                    advisory_warnings.append(
                        f"⚠️ Fuzzy match used (strategy: {strategy}) for: '{search_str[:40]}...'"
                    )

            if not_found and total_replaced == 0:
                return Response(
                    message=_build_not_found_error(path, not_found),
                    break_loop=False,
                )

            # ── ADR-011: Mock Data Detection on final content (advisory) ──
            mock_warning = detect_mock_data(content, abs_path)
            if mock_warning:
                advisory_warnings.append(mock_warning)

            # Atomic write
            files.write_file_atomic(abs_path, content)

            # FIX-12: Broadcast write for cross-agent stale detection
            record_file_write(agent_id, abs_path)

            # ── WriteLedger: Track this write for post-batch verification ──
            try:
                from python.helpers.write_ledger import WriteLedger
                project_name = projects.get_context_project_name(self.agent.context)
                if project_name:
                    project_dir = projects.get_project_folder(project_name)
                    agent_id = str(getattr(self.agent, 'number', 'unknown'))
                    WriteLedger().record_write(project_dir, abs_path, agent_id)
            except Exception:
                pass  # Ledger is advisory; never block file writes

            msg = f"Successfully replaced {total_replaced} occurrence(s) in '{path}'."
            if not_found:
                msg += f" Warning: {len(not_found)} search string(s) not found and skipped."
            if advisory_warnings:
                msg += "\n\n" + "\n\n".join(advisory_warnings)
            return Response(message=msg, break_loop=False)

        except Exception as e:
            return Response(message=f"Error replacing in file '{path}': {str(e)}", break_loop=False)

    def _resolve_pairs(self):
        """Resolve replacement pairs from either the batch or legacy API format.

        Batch API (from prompt docs):
            {"replacements": [{"search": "old", "replace": "new"}, ...]}

        Legacy single-pair API:
            {"search_string": "old", "replace_string": "new"}

        Returns:
            (list[tuple[str, str]], str | None): pairs and optional error message
        """
        replacements = self.args.get("replacements")

        # ── Batch API ──
        if replacements is not None:
            if not isinstance(replacements, list) or len(replacements) == 0:
                return [], (
                    "Error: 'replacements' must be a non-empty array of "
                    "{\"search\": \"...\", \"replace\": \"...\"} objects."
                )
            pairs = []
            for i, r in enumerate(replacements):
                s = r.get("search") or r.get("search_string")
                rp = r.get("replace") or r.get("replace_string")
                if s is None:
                    return [], f"Error: replacements[{i}] missing 'search' key."
                if rp is None:
                    rp = ""  # Allow replacing with empty string (deletion)
                pairs.append((s, rp))
            return pairs, None

        # ── Legacy single-pair API ──
        search_string = self.args.get("search_string") or self.args.get("search")
        replace_string = self.args.get("replace_string") or self.args.get("replace")

        if search_string is None:
            return [], (
                "Error: Missing 'search_string' (or 'replacements' array). "
                "Valid formats:\n"
                "  1) {\"search_string\": \"text\", \"replace_string\": \"new\"}\n"
                "  2) {\"replacements\": [{\"search\": \"text\", \"replace\": \"new\"}]}"
            )
        if replace_string is None:
            replace_string = ""

        return [(search_string, replace_string)], None


# ── R2: 3-Strategy Match Cascade (RCA-316) ──
# Modeled after Roo-Code's EditFileTool.ts approach:
#   Strategy 1: Exact string match (fast path)
#   Strategy 2: Whitespace-tolerant regex (tabs↔spaces, trailing WS, CRLF)
#   Strategy 3: Token-based regex (indentation-independent matching)

def _apply_replacement(content: str, search: str, replace: str) -> tuple:
    """3-strategy match cascade: exact → whitespace-tolerant → token-based.

    Args:
        content: The file content to search in.
        search: The search string from the user.
        replace: The replacement string.

    Returns:
        (new_content, match_count, strategy_name)
    """
    # Strategy 1: Exact match (fast path — zero overhead for common case)
    if search in content:
        count = content.count(search)
        return content.replace(search, replace), count, "exact"

    # Strategy 2: Whitespace-tolerant regex
    ws_pattern = _build_whitespace_tolerant_regex(search)
    if ws_pattern:
        new_content, count = ws_pattern.subn(replace, content)
        if count > 0:
            return new_content, count, "whitespace-tolerant"

    # Strategy 3: Token-based regex
    token_pattern = _build_token_regex(search)
    if token_pattern:
        new_content, count = token_pattern.subn(replace, content)
        if count > 0:
            return new_content, count, "token-based"

    return content, 0, "none"


def _build_whitespace_tolerant_regex(search: str):
    """Build regex that treats any whitespace run as \\s+.

    Splits the search string on whitespace runs and joins with \\s+
    to match tabs↔spaces, trailing whitespace, and CRLF differences.

    Returns:
        Compiled regex pattern or None if search is empty.
    """
    if not search or not search.strip():
        return None

    # Split into non-whitespace tokens separated by whitespace
    parts = re.split(r'\s+', search)
    # Remove empty parts from leading/trailing whitespace
    parts = [p for p in parts if p]
    if not parts:
        return None

    # Escape each literal part for regex safety
    escaped_parts = [re.escape(p) for p in parts]
    # Join with \s+ to allow any whitespace between tokens
    pattern_str = r'\s+'.join(escaped_parts)

    try:
        return re.compile(pattern_str, re.DOTALL)
    except re.error:
        return None


def _build_token_regex(search: str):
    """Build regex from non-whitespace tokens, allowing flexible whitespace.

    More aggressive than whitespace-tolerant: allows \\s* between tokens,
    enabling matches even when indentation has been completely reorganized.

    Returns:
        Compiled regex pattern or None if search is empty/whitespace-only.
    """
    if not search or not search.strip():
        return None

    # Split into tokens (non-whitespace sequences)
    tokens = re.findall(r'\S+', search)
    if not tokens:
        return None

    # Escape each token and join with \s* (zero or more whitespace)
    escaped_tokens = [re.escape(t) for t in tokens]
    pattern_str = r'\s*'.join(escaped_tokens)

    try:
        return re.compile(pattern_str, re.DOTALL)
    except re.error:
        return None


def _build_not_found_error(path: str, not_found: list) -> str:
    """Build structured error with numbered recovery steps.

    Provides actionable guidance rather than a bare "not found" message,
    helping agents self-correct without human intervention.
    """
    preview = "; ".join(not_found)
    return (
        f"Error: Could not find search string(s) in file '{path}'.\n"
        f"Not found: [{preview}]\n\n"
        f"Recovery steps:\n"
        f"1. Use `read_file` to confirm the file's current contents\n"
        f"2. Ensure search strings match EXACTLY (including whitespace/indentation)\n"
        f"3. Provide more surrounding context in your search string\n"
        f"4. If the file has changed since you read it, re-read and retry\n"
        f"5. For large structural changes, consider using `write_to_file` instead"
    )


# ── RCA-462 Finding 3: Structural Damage Prevention ──
# Detects dangerous replacement patterns that would destroy code structure.
# Called before each replacement to generate advisory warnings.

# Patterns that indicate code structure (not content)
_CODE_STRUCTURE_PATTERNS = [
    re.compile(r'\b(?:function|class|const|let|var|export|import|def|async)\s+.*\b{search}\b', re.IGNORECASE),
    re.compile(r'from\s+[\'"].*{search}.*[\'"]', re.IGNORECASE),
    re.compile(r'import\s+.*{search}', re.IGNORECASE),
]


def _detect_structural_risk(
    search: str,
    replace: str,
    content: str,
) -> str | None:
    """Detect if a replacement would cause structural damage to code.

    RCA-462: The code agent uses replace_in_file with short, ambiguous search
    strings like "Compliant" that match in function names, destroying code:
        function CompliantPage() → function TCPA Compliant. We integrate...Page()

    Checks:
    1. Search string too short (<10 chars) AND matches in code structure
    2. Search matches in function/class names, imports, or variable declarations
    3. Search matches 3+ locations (high collateral damage risk)
    4. Replacement is >5x longer than search (content injection pattern)

    Returns:
        Advisory warning string if risk detected, None if safe.
    """
    if not search or not content:
        return None

    search_len = len(search.strip())
    replace_len = len(replace.strip()) if replace else 0

    # Count occurrences
    match_count = content.count(search)
    if match_count == 0:
        return None

    warnings = []

    # ── Check 1: Short search string + multiple matches ──
    if search_len < 10 and match_count > 1:
        warnings.append(
            f"⚠️ STRUCTURAL RISK: Short search string '{search}' ({search_len} chars) "
            f"matches {match_count} locations. Use a longer, more specific search "
            f"string to avoid replacing code identifiers (function names, variables, imports)."
        )

    # ── Check 2: Search string appears in code structure ──
    # Check if any line containing the search string looks like code structure
    for line in content.split('\n'):
        if search not in line:
            continue
        stripped = line.strip()
        # Function/class/variable declarations
        if re.match(r'^\s*(?:export\s+)?(?:default\s+)?(?:function|class|const|let|var|async\s+function)\s+', stripped):
            if search in stripped.split('(')[0] if '(' in stripped else search in stripped:
                warnings.append(
                    f"⚠️ STRUCTURAL RISK: Search string '{search}' appears in a code "
                    f"identifier/declaration: `{stripped[:80]}`. Replacing this will "
                    f"destroy the function/variable name. Use a more specific search "
                    f"that targets only JSX text content (e.g., include surrounding tags)."
                )
                break
        # Import statements
        if re.match(r'^\s*(?:import|from)\s+', stripped):
            warnings.append(
                f"⚠️ STRUCTURAL RISK: Search string '{search}' appears in an import "
                f"statement: `{stripped[:80]}`. Replacing this will break module resolution."
            )
            break

    # ── Check 3: Multiple match locations ──
    if match_count >= 3 and not warnings:
        warnings.append(
            f"⚠️ STRUCTURAL RISK: Search string '{search}' matches {match_count} "
            f"locations in the file. Use a more specific search string (include "
            f"surrounding JSX tags or code context) to target only the intended location."
        )

    # ── Check 4: Extreme length ratio (content injection) ──
    if search_len > 0 and replace_len > search_len * 5 and search_len < 20:
        if not warnings:  # Don't duplicate if already flagged
            warnings.append(
                f"⚠️ STRUCTURAL RISK: Replacement ({replace_len} chars) is "
                f"{replace_len // search_len}x longer than search ({search_len} chars). "
                f"This expansion ratio suggests content injection. Use a more specific "
                f"search string that includes surrounding code context."
            )

    return "\n".join(warnings) if warnings else None
