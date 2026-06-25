"""Save deliverable tool — specialist agents persist their output as MD files.

Each specialist agent (researcher, account-leader, marketing-lead, sales-enabler)
calls this tool to write their final output to the project's deliverables/ directory
so the content-writer can later read and synthesize them.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from python.helpers import files, projects
from python.helpers.budget_cost_model import validate_dependency_graph
from python.helpers.print_style import PrintStyle
from python.helpers.tool import Tool, Response

logger = logging.getLogger("save_deliverable")


DELIVERABLES_DIR = "deliverables"


class SaveDeliverable(Tool):
    """Write specialist agent output as an MD file to the project deliverables dir."""

    @staticmethod
    def _resolve_title(title: str, content: str) -> str:
        """Auto-generate title if empty.

        Resolution order:
        1. Explicit title (if provided)
        2. First H1/H2 heading in content
        3. First 60 chars of content
        4. 'Untitled Deliverable' fallback
        """
        if title and title.strip():
            return title.strip()

        if content and content.strip():
            # Try first H1 or H2 heading
            heading_match = re.search(r'^#{1,2}\s+(.+)$', content, re.MULTILINE)
            if heading_match:
                return heading_match.group(1).strip()

            # Fallback: first 60 chars of content
            first_line = content.strip().split('\n')[0].strip()
            return first_line[:60]

        return "Untitled Deliverable"

    # ── RCA-ITR50: Well-known deliverable title → output_path mappings ──
    # When the agent forgets to set output_path, auto-infer it from the title.
    # This is a Layer 1 deterministic safety net — it catches the common case
    # where the LLM uses title="Design Tokens" but omits output_path.
    _KNOWN_DELIVERABLE_PATTERNS: list[tuple[re.Pattern, str, str]] = [
        # (title_regex, output_path_for_json_content, output_path_for_md_content)
        (re.compile(r"design[\s_-]*tokens?", re.IGNORECASE), "design-tokens.json", "design-tokens.md"),
        (re.compile(r"component[\s_-]*spec", re.IGNORECASE), "component-spec.md", "component-spec.md"),
        (re.compile(r"^architecture$", re.IGNORECASE), "docs/architecture.md", "docs/architecture.md"),
        (re.compile(r"framework[\s_-]*research", re.IGNORECASE), "docs/framework-research.md", "docs/framework-research.md"),
    ]

    @staticmethod
    def _infer_output_path(title: str, content: str, output_path: str) -> str:
        """Auto-infer output_path from well-known deliverable titles.

        RCA-ITR50: The frontend agent called save_deliverable(title="Design Tokens")
        without output_path, so design-tokens.json was never created — only a
        timestamped .md in deliverables/. This caused ARCH-RCSIG to block Phase 3.

        This method is a Layer 1 safety net: when output_path is empty but the
        title matches a known deliverable pattern, auto-infer the canonical path.

        Args:
            title: The deliverable title
            content: The deliverable content
            output_path: The explicit output_path (if provided)

        Returns:
            The inferred output_path, or the original output_path if already set.
        """
        # Don't override explicit output_path
        if output_path and output_path.strip():
            return output_path.strip()

        if not title or not title.strip():
            return output_path or ""

        normalized_title = " ".join(title.strip().split())  # collapse whitespace

        # Check if content looks like JSON
        content_stripped = (content or "").strip()
        is_json_content = content_stripped.startswith("{") or content_stripped.startswith("[")

        for pattern, json_path, md_path in SaveDeliverable._KNOWN_DELIVERABLE_PATTERNS:
            if pattern.search(normalized_title):
                inferred = json_path if is_json_content else md_path
                logger.info(
                    f"[save_deliverable] RCA-ITR50: Auto-inferred output_path="
                    f"'{inferred}' from title='{title}' "
                    f"(json_content={is_json_content})"
                )
                return inferred

        return output_path or ""

    @staticmethod
    def _try_read_file_fallback(file_path: str, content: str) -> str:
        """Self-healing fallback: read file content when content arg is empty.

        Root Cause (5-Why for 'content required' on existing .md files):
        1. Agent passed file_path + description instead of inline content
        2. Agent assumed the tool would read the file (reasonable assumption)
        3. Tool had no file-reading fallback — only accepted inline content
        4. This caused spurious rejections on legitimate deliverables

        Resolution: When content is empty/whitespace-only but file_path
        references an existing readable file, read and return its content.
        If content is already non-empty, return it unchanged (no override).
        """
        # If content already has substance, don't override
        if content and content.strip():
            return content

        # Try reading from file_path
        if not file_path or not file_path.strip():
            return content or ""

        file_path = file_path.strip()
        if not os.path.isfile(file_path):
            return content or ""

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                file_content = f.read()
            if file_content and file_content.strip():
                return file_content
        except (IOError, OSError):
            pass

        return content or ""

    async def execute(self, **kwargs) -> Response:
        title = self.args.get("title", "")
        content = self.args.get("content", "")
        agent_role = self.args.get("agent_role", "")
        output_path = self.args.get("output_path", "")

        # FIX-04: Auto-generate title if empty
        title = self._resolve_title(title, content)

        # RCA-ITR50: Auto-infer output_path from well-known titles
        # Must run AFTER title resolution but BEFORE content fallbacks
        output_path = self._infer_output_path(title, content, output_path)

        # Self-healing: if content is empty but file_path provided, read the file
        file_path = self.args.get("file_path", "") or self.args.get("path", "")
        content = self._try_read_file_fallback(file_path, content)

        # FIX-7: Extended fallback — if content is still empty and output_path
        # points to an existing file, read from there. The architect sometimes
        # calls save_deliverable with output_path (canonical destination) but
        # empty content, expecting the tool to "sync" or "save" an existing file.
        # Root cause: replace_in_deliverable edited the file on disk, then the
        # architect called save_deliverable without re-reading the content.
        if (not content or not content.strip()) and output_path and output_path.strip():
            try:
                from python.helpers.resolve_agent_path import resolve_agent_path
                proj_dir = resolve_agent_path("", self.agent)
                candidate = os.path.normpath(os.path.join(proj_dir, output_path.strip()))
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 50:
                    with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
                        disk_content = f.read()
                    if disk_content and disk_content.strip():
                        content = disk_content
                        PrintStyle.hint(
                            f"[save_deliverable] FIX-7: Read content from existing "
                            f"output_path '{output_path}' ({len(content)} chars)"
                        )
            except Exception as e:
                logger.warning(
                    f"[save_deliverable] FIX-7 content fallback failed: {e}. "
                    f"output_path='{output_path}'"
                )

        # Validate content

        if not content or not content.strip():
            return Response(
                message="Error: 'content' is required and cannot be empty. "
                        "Provide the full deliverable content to save.",
                break_loop=True,
            )

        # Auto-detect agent role from profile if not explicitly provided
        if not agent_role:
            try:
                agent_role = self.agent.config.profile or "unknown"
            except AttributeError:
                agent_role = "unknown"

        # ISS-4: Use canonical resolve_agent_path for project root resolution.
        try:
            from python.helpers.resolve_agent_path import resolve_agent_path, ProjectContextError
            project_dir = resolve_agent_path("", self.agent)  # Empty path = project root
        except (ProjectContextError, Exception) as e:
            logger.warning(
                f"[save_deliverable] resolve_agent_path failed: {e}. "
                f"Falling back to tmp/ for output_path='{output_path}'"
            )
            project_dir = files.get_abs_path("tmp")

        deliverables_dir = os.path.join(project_dir, DELIVERABLES_DIR)
        os.makedirs(deliverables_dir, exist_ok=True)

        # Build filename: agent-role_YYYYMMDD_HHMMSS.md
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        safe_role = agent_role.replace(" ", "-").lower()
        filename = f"{safe_role}_{timestamp_str}.md"
        filepath = os.path.join(deliverables_dir, filename)

        # Build YAML frontmatter
        frontmatter = (
            f"---\n"
            f"agent: {safe_role}\n"
            f"title: {title}\n"
            f"timestamp: {now.isoformat()}\n"
            f"---\n\n"
        )

        # ── ISS-5/6/7 FIX: output_path support for canonical file placement ──
        # When output_path is provided, write to the canonical project path
        # AND a copy to deliverables/ for backward compatibility.
        canonical_path = None
        if output_path and output_path.strip():
            output_path = output_path.strip()

            # Security: reject absolute paths
            if os.path.isabs(output_path):
                return Response(
                    message=f"Error: output_path must be a relative path, "
                            f"not an absolute path: '{output_path}'",
                    break_loop=True,
                )

            # Security: resolve and validate path stays within project sandbox
            canonical_path = os.path.normpath(os.path.join(project_dir, output_path))
            if not canonical_path.startswith(os.path.normpath(project_dir) + os.sep) and \
               canonical_path != os.path.normpath(project_dir):
                return Response(
                    message=f"Error: output_path '{output_path}' resolves outside "
                            f"the project directory (path traversal blocked).",
                    break_loop=True,
                )

            # Create parent directories for canonical path
            os.makedirs(os.path.dirname(canonical_path), exist_ok=True)

            # Determine file extension — ONLY .md files get YAML frontmatter.
            # All other extensions (.ts, .tsx, .js, .prisma, .css, .json,
            # .yaml, .yml, .html, .py, .sql, .sh, .env, etc.) are corrupted
            # by frontmatter (causes 'Expression expected' build failures).
            ext = os.path.splitext(canonical_path)[1].lower()
            if ext == ".md":
                canonical_content = frontmatter + content
            else:
                canonical_content = content

            # Sanitize surrogates for canonical file
            try:
                canonical_content.encode('utf-8')
            except UnicodeEncodeError:
                canonical_content = canonical_content.encode(
                    'utf-8', errors='surrogatepass'
                ).decode('utf-8', errors='ignore')

            with open(canonical_path, "w", encoding="utf-8") as f:
                f.write(canonical_content)

        # Write to deliverables/ (always — backward compat with read_deliverables)
        full_content = frontmatter + content
        try:
            full_content.encode('utf-8')
        except UnicodeEncodeError:
            full_content = full_content.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='ignore')
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_content)

        # Wire 5: Post-save hooks (advisory validation for dependency graphs)
        try:
            SaveDeliverable._run_post_save_hooks(
                output_path=output_path or "",
                content=content,
                project_dir=project_dir,
            )
        except Exception as e:
            logger.warning(f"[save_deliverable] Post-save hook error (non-blocking): {e}")

        # Build response message
        if canonical_path:
            msg = (
                f"✅ Deliverable saved: {canonical_path}\n"
                f"   (also copied to: {filepath})\n"
                f"Agent: {safe_role} | Title: {title} | "
                f"Size: {len(content)} chars"
            )
        else:
            msg = (
                f"✅ Deliverable saved: {filepath}\n"
                f"Agent: {safe_role} | Title: {title} | "
                f"Size: {len(content)} chars"
            )
        PrintStyle.hint(msg)

        return Response(
            message=msg,
            break_loop=False,
        )

    @staticmethod
    def _run_post_save_hooks(
        output_path: str,
        content: str,
        project_dir: str,
    ) -> None:
        """Run advisory post-save hooks on saved deliverables.

        Wire 5: When the architect saves a dependency-graph.json,
        validate its structural integrity. Validation is advisory —
        errors are logged as warnings but never block the save.

        Args:
            output_path: The relative output path of the saved file.
            content: The raw content that was saved.
            project_dir: The project root directory.
        """
        if not output_path:
            return

        # Only trigger for dependency-graph.json files
        basename = os.path.basename(output_path)
        if basename != "dependency-graph.json":
            return

        # Parse and validate the dependency graph
        try:
            graph = json.loads(content)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                f"[save_deliverable] Wire-5: dependency-graph.json is not valid JSON: {e}"
            )
            return

        errors = validate_dependency_graph(graph)
        if errors:
            logger.warning(
                f"[save_deliverable] Wire-5: dependency-graph.json has "
                f"{len(errors)} validation issues (advisory): {errors[:3]}"
            )
        else:
            logger.info(
                "[save_deliverable] Wire-5: dependency-graph.json validated OK"
            )
