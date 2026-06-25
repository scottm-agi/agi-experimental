from __future__ import annotations
import os
from python.helpers.tool import Tool, Response
from python.helpers import files, projects
from python.helpers.file_guard import FileGuard
from python.helpers.content_regression_guard import check_content_regression
from python.helpers.read_before_write_guard import check_read_before_write, check_read_before_write_proactive, record_file_write
from python.helpers.mock_data_detector import detect_mock_data
from python.helpers.line_number_detector import detect_line_number_corruption
from python.helpers.content_type_guard import detect_content_type_mismatch


class WriteToFile(Tool):
    async def execute(self, **kwargs) -> Response:
        path = self.args.get("path")
        content = self.args.get("content")
        overwrite = self.args.get("overwrite", True)
        overwrite_force = self.args.get("overwrite_force", False)

        if not path:
            return Response(message="Error: Missing 'path' argument.", break_loop=False)
        if content is None:
            return Response(message="Error: Missing 'content' argument.", break_loop=False)

        try:
            # ISS-4: Use canonical project-aware resolver for relative paths.
            # Previously used files.get_abs_path() which resolves to framework root.
            if not os.path.isabs(path):
                from python.helpers.resolve_agent_path import resolve_agent_path
                abs_path = resolve_agent_path(path, self.agent)
            else:
                abs_path = path

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
                path = resolved_path  # Update for response message too

            if os.path.exists(abs_path) and not overwrite:
                return Response(message=f"Error: File '{path}' already exists and overwrite is set to False.", break_loop=False)

            # ── Read-Before-Write Guard: Ensure agent read the file first ──
            agent_id = str(getattr(self.agent, 'number', 'unknown'))
            advisory_warnings = []

            # ADR-010: Try proactive guard first (auto-reads and advises)
            proactive_result = check_read_before_write_proactive(
                agent_id=agent_id,
                abs_path=abs_path,
                force=overwrite_force,
            )
            if proactive_result:
                advisory_warnings.append(proactive_result.warning)
            else:
                # Fall back to blocking guard for edge cases
                rbw_msg = check_read_before_write(
                    agent_id=agent_id,
                    abs_path=abs_path,
                    force=overwrite_force,
                )
                if rbw_msg:
                    return Response(message=rbw_msg, break_loop=False)

            # ── Content Regression Guard: Prevent silent content loss ──
            regression_msg = check_content_regression(
                abs_path=abs_path,
                new_content=content,
                force=overwrite_force,
            )
            if regression_msg:
                return Response(message=regression_msg, break_loop=False)

            # ── ADR-011: Mock Data Detection (advisory) ──
            mock_warning = detect_mock_data(content, abs_path)
            if mock_warning:
                advisory_warnings.append(mock_warning)

            # ── RCA-248: Line-Number Corruption Detection (advisory) ──
            corruption_warning = detect_line_number_corruption(content)
            if corruption_warning:
                advisory_warnings.append(corruption_warning)

            # ── RCA-262: Content-Type Mismatch Detection (advisory) ──
            content_type_warning = detect_content_type_mismatch(abs_path, content)
            if content_type_warning:
                advisory_warnings.append(content_type_warning)

            # Ensure directory exists
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)

            # Atomic write
            files.write_file_atomic(abs_path, content)

            # FIX-12: Broadcast write for cross-agent stale detection
            record_file_write(agent_id, abs_path)

            # ── SS-2 Post-Write Verification: confirm file landed on disk ──
            if not os.path.exists(abs_path):
                return Response(
                    message=f"ERROR: write_to_file wrote to '{path}' but file does NOT exist on disk at '{abs_path}'. "
                            f"This is a critical file system error — the write was silently lost.",
                    break_loop=False
                )

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

            # Build response with any advisory warnings
            msg = f"Successfully wrote to file '{path}'."
            if advisory_warnings:
                msg += "\n\n" + "\n\n".join(advisory_warnings)

            return Response(message=msg, break_loop=False)

        except Exception as e:
            return Response(message=f"Error writing to file '{path}': {str(e)}", break_loop=False)

