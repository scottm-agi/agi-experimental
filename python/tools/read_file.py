from __future__ import annotations
import os
from python.helpers.tool import Tool, Response
from python.helpers import files
from python.helpers.read_before_write_guard import record_file_read
from python.helpers.resolve_agent_path import resolve_agent_path, ProjectContextError

DEFAULT_MAX_LINES = 2000

class ReadFile(Tool):
    async def execute(self, **kwargs) -> Response:
        files_to_read = self.args.get("files", [])
        if not files_to_read and "path" in self.args:
            files_to_read = [{"path": self.args.get("path"), "line_ranges": None}]
        
        if not files_to_read:
            return Response(message="Error: Missing 'files' or 'path' argument.", break_loop=False)

        max_lines_per_file = int(self.args.get("max_lines", DEFAULT_MAX_LINES))
        results = []

        for file_info in files_to_read:
            path = file_info.get("path")
            line_ranges = file_info.get("line_ranges") # List of [start, end]
            
            try:
                # ── RCA-318: Canonical project-aware path resolution ──
                # Relative paths resolve against the active project dir (ONLY).
                # Absolute paths are used as-is.
                try:
                    abs_path = resolve_agent_path(path, self.agent)
                except ProjectContextError as e:
                    # No project context — warn agent, don't block
                    results.append(
                        f"⚠️ PATH RESOLUTION ERROR: {e}\n"
                        f"Cannot resolve relative path '{path}' without project context."
                    )
                    continue

                if not os.path.exists(abs_path):
                    results.append(f"Error: File '{path}' not found.")
                    continue

                with open(abs_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                total_lines = len(lines)
                
                if line_ranges:
                    content_parts = []
                    for r in line_ranges:
                        start = int(r[0]) - 1
                        end = int(r[1])
                        start = max(0, min(start, total_lines))
                        end = max(start, min(end, total_lines))
                        content_parts.append("".join(lines[start:end]))
                    content = "\n---\n".join(content_parts)
                    range_info = f" (multiple ranges, {total_lines} lines total)"
                    truncated = False
                else:
                    if total_lines > max_lines_per_file:
                        content = "".join(lines[:max_lines_per_file])
                        truncated = True
                        range_info = f" (lines 1-{max_lines_per_file} of {total_lines}, truncated)"
                    else:
                        content = "".join(lines)
                        range_info = f" ({total_lines} lines)"
                        truncated = False

                res = f"File: {path}{range_info}\n\n{content}"
                if truncated:
                    next_start = max_lines_per_file + 1
                    res += f"\n\n[TRUNCATED] To read more, use start_line={next_start} (or end_line if using ranges)."
                
                # Track this read for read-before-write enforcement
                agent_id = str(getattr(self.agent, 'number', 'unknown'))
                record_file_read(agent_id, abs_path)

                results.append(res)

            except Exception as e:
                results.append(f"Error reading file '{path}': {str(e)}")

        return Response(message="\n\n" + "="*40 + "\n\n".join(results), break_loop=False)

