from __future__ import annotations
import os
import re
import logging
from typing import Dict, Any, List, Optional, Set
from python.helpers.tool import Tool, Response
from python.helpers import files
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("architecture-analysis")

class ArchitectureAnalysis(Tool):
    """
    Analyzes the repository structure, identifies core components, 
    and generates a high-level architectural overview with a Mermaid diagram.
    """

    def __init__(self, agent: Agent, name: str, method: str | None, args: dict, message: str, loop_data: LoopData | None, **kwargs):
        super().__init__(agent, name, method, args, message, loop_data, **kwargs)
        self.exclude_dirs = {
            ".git", "node_modules", "venv", "__pycache__", "tmp", "dist", "build", ".gemini",
            ".venv-test", ".pytest_cache", ".smart-coding-cache", ".roo", "logs", "delete"
        }
        self.max_depth = 3
        self.entry_point_patterns = [
            r"main\.py$", r"app\.py$", r"server\.py$", r"run_.*\.py$", r"index\.js$", r"start\.sh$"
        ]

    async def execute(self, **kwargs) -> Response:
        root_dir = os.getcwd() # Assume current working directory is the repo root
        
        # 1. Map repository structure
        repo_map = self._map_repository(root_dir)
        
        # 2. Identify Entry Points
        entry_points = self._identify_entry_points(repo_map)
        
        # 3. Analyze Dependencies (High-level)
        dependencies = self._analyze_dependencies(repo_map)
        
        # 4. Generate Mermaid Diagram
        mermaid_chart = self._generate_mermaid(entry_points, dependencies)
        
        # 5. Formulate Report
        report = self._format_report(entry_points, repo_map, mermaid_chart)
        
        return Response(
            message=report,
            break_loop=False
        )

    def _map_repository(self, root: str) -> Dict[str, List[str]]:
        """Maps directories to their contents with a depth limit."""
        mapping = {}
        for root_dir, dirs, files_list in os.walk(root):
            rel_path = os.path.relpath(root_dir, root)
            depth = 0 if rel_path == "." else rel_path.count(os.sep) + 1
            
            if depth > self.max_depth:
                del dirs[:] # Stop recursion
                continue

            # Filter excluded dirs
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            
            if rel_path == ".":
                rel_path = "root"
            
            mapping[rel_path] = files_list
        return mapping

    def _identify_entry_points(self, repo_map: Dict[str, List[str]]) -> List[str]:
        """Finds likely entry points based on filenames."""
        entries = []
        for folder, files_list in repo_map.items():
            for f in files_list:
                if any(re.search(pat, f) for pat in self.entry_point_patterns):
                    path = f if folder == "root" else os.path.join(folder, f)
                    entries.append(path)
        return entries

    def _analyze_dependencies(self, repo_map: Dict[str, List[str]]) -> Dict[str, Set[str]]:
        """Extracts high-level module dependencies (e.g., frontend -> backend)."""
        deps = {}
        # Simple heuristic: cross-directory imports in Python files
        for folder, files_list in repo_map.items():
            if folder == "root": continue
            
            current_module = folder.split(os.sep)[0]
            if current_module not in deps:
                deps[current_module] = set()
            
            for f in files_list:
                if f.endswith(".py"):
                    full_path = os.path.join(folder, f)
                    try:
                        with open(full_path, "r", errors="ignore") as file:
                            content = file.read()
                            # Look for imports from other top-level directories
                            for other in repo_map.keys():
                                if other == "root": continue
                                other_module = other.split(os.sep)[0]
                                if other_module != current_module:
                                    # Search for 'import other_module' or 'from other_module'
                                    if re.search(fr"\b(from|import)\s+{other_module}\b", content):
                                        deps[current_module].add(other_module)
                    except Exception:
                        continue
        return deps

    def _generate_mermaid(self, entry_points: List[str], dependencies: Dict[str, Set[str]]) -> str:
        """Generates a Mermaid flowchart."""
        lines = ["graph TD"]
        
        # Add entry points as start nodes
        for ep in entry_points:
            node_id = ep.replace(".", "_").replace("/", "_").replace("-", "_")
            lines.append(f'  {node_id}["{ep}"]')
            
            # Link entry point to its parent module if applicable
            parts = ep.split(os.sep)
            if len(parts) > 1:
                module = parts[0]
                lines.append(f"  {node_id} --> {module}")
        
        # Add module dependencies
        for module, module_deps in dependencies.items():
            for dep in module_deps:
                lines.append(f"  {module} --> {dep}")
        
        # If no dependencies found, add a simple directory tree hint
        if len(lines) == 1:
            lines.append("  Root --> Details[Check Summary Below]")
            
        return "\n".join(lines)

    def _format_report(self, entry_points: List[str], repo_map: Dict[str, List[str]], mermaid: str) -> str:
        """Formats the final architectural report."""
        report = "# Architecture Overview\n\n"
        
        report += "## System Diagram\n"
        report += "```mermaid\n"
        report += mermaid + "\n"
        report += "```\n\n"
        
        report += "## Entry Points\n"
        if entry_points:
            report += "\n".join([f"- `{ep}`" for ep in entry_points]) + "\n"
        else:
            report += "No obvious entry points detected.\n"
        
        report += "\n## Core Components\n"
        top_level = sorted({k.split(os.sep)[0] for k in repo_map.keys() if k != "root"})
        for tl in top_level:
            report += f"- **{tl}**: Primary component directory.\n"
        
        return report
