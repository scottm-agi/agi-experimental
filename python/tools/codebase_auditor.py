from __future__ import annotations
import os
import re
import logging
import json
from typing import Dict, Any, List, Optional, Set
from python.helpers.tool import Tool, Response
from python.helpers import files
from python.tools.repo_automation.forgejo import create_issue_forgejo
from python.tools.repo_automation.github import create_issue_github
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("codebase-auditor")

class CodebaseAuditor(Tool):
    """
    Performs a multi-dimensional audit of a codebase (security, architecture, performance)
    and generates an AI-driven refactoring report.
    """

    def __init__(self, agent: Agent, name: str, method: str | None, args: dict, message: str, loop_data: LoopData | None, **kwargs):
        super().__init__(agent, name, method, args, message, loop_data, **kwargs)
        self.path = args.get("path", os.getcwd())
        self.post_issue = args.get("post_issue", False)
        self.repo_type = args.get("repo_type", "forgejo") # or 'github'
        
        self.exclude_dirs = {
            ".git", "node_modules", "venv", "__pycache__", "tmp", "dist", "build", ".gemini",
            ".venv-test", ".pytest_cache", ".smart-coding-cache", ".roo", "logs", "delete"
        }
        
        # Security Patterns
        self.secret_patterns = {
            "Hardcoded Password": r"(password|passwd|pwd|secret)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
            "Generic API Key": r"(api_key|apikey|access_token|secret_key)\s*[:=]\s*['\"][A-Za-z0-9\-_]{16,}['\"]",
            "AWS Secret Access Key": r"(AWS_SECRET_ACCESS_KEY|aws_secret)\s*[:=]\s*['\"][A-Za-z0-9/+=]{40}['\"]"
        }
        
        # Performance Patterns
        self.perf_patterns = {
            "Nested Loops": r"(for|while).*\n\s+(for|while).*\n\s+\s+(for|while)",
            "Large Regexes": r"re\.compile\(r?['\"].{200,},",
        }

    async def execute(self, **kwargs) -> Response:
        if not os.path.exists(self.path):
            return Response(message=f"Error: Path '{self.path}' does not exist.", break_loop=False)

        # 1. Structural Scan
        structure = self._scan_structure()
        
        # 2. Heuristic Scans (Security & Performance)
        security_findings = self._scan_heuristics(self.secret_patterns)
        perf_findings = self._scan_heuristics(self.perf_patterns)
        
        # 3. Size Scan
        large_files = self._scan_file_sizes()

        # 4. Generate AI Analysis
        expert_analysis = await self._generate_expert_analysis(structure, security_findings, perf_findings, large_files)

        # 5. Format Final Report
        report = self._format_markdown_report(structure, security_findings, perf_findings, large_files, expert_analysis)

        # 6. Post Issue if requested
        post_status = ""
        if self.post_issue:
            post_status = await self._post_to_remote(report)

        return Response(
            message=f"{report}\n\n{post_status}".strip(),
            break_loop=False
        )

    def _scan_structure(self) -> Dict[str, Any]:
        """Identifies entry points and core modules."""
        entry_points = []
        modules = set()
        
        entry_patterns = [r"main\.py$", r"app\.py$", r"server\.py$", r"index\.js$", r"start\.sh$"]

        for root_dir, dirs, files_list in os.walk(self.path):
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            rel_path = os.path.relpath(root_dir, self.path)
            
            if rel_path != ".":
                modules.add(rel_path.split(os.sep)[0])

            for f in files_list:
                if any(re.search(pat, f) for pat in entry_patterns):
                    entry_points.append(os.path.join(rel_path, f))

        return {
            "entry_points": entry_points,
            "modules": sorted(list(modules))
        }

    def _scan_heuristics(self, patterns: Dict[str, str]) -> List[Dict[str, str]]:
        """Scans files for regex patterns."""
        findings = []
        for root_dir, dirs, files_list in os.walk(self.path):
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            for f in files_list:
                if not f.endswith((".py", ".js", ".ts", ".sh", ".env", ".json")):
                    continue
                
                path = os.path.join(root_dir, f)
                try:
                    with open(path, "r", errors="ignore") as file:
                        content = file.read()
                        for name, pat in patterns.items():
                            if re.search(pat, content, re.IGNORECASE):
                                rel = os.path.relpath(path, self.path)
                                findings.append({"type": name, "file": rel})
                except Exception:
                    continue
        return findings

    def _scan_file_sizes(self, threshold_mb: float = 0.5) -> List[Dict[str, Any]]:
        """Identifies files above a certain size threshold."""
        large_files = []
        for root_dir, dirs, files_list in os.walk(self.path):
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            for f in files_list:
                path = os.path.join(root_dir, f)
                size_mb = os.path.getsize(path) / (1024 * 1024)
                if size_mb > threshold_mb:
                    rel = os.path.relpath(path, self.path)
                    large_files.append({"file": rel, "size": round(size_mb, 2)})
        return large_files

    async def _generate_expert_analysis(self, structure: Dict, security: List, perf: List, large: List) -> Dict:
        """Calls LLM for high-level refactoring advice."""
        prompt = f"""You are a senior software architect. Provide a high-level refactoring strategy for a codebase with the following audit data:

Structure: {json.dumps(structure)}
Security Findings: {json.dumps(security)}
Performance Findings: {json.dumps(perf)}
Large Files: {json.dumps(large)}

Return ONLY a JSON object:
{{
  "refactoring_summary": "<strategy>",
  "mermaid_diagram": "<graph TD diagram of the core modules>"
}}
"""
        from python.helpers.call_llm import call_llm
        from python.models import get_chat_model
        
        chat_config = self.agent.config.chat_model
        chat_model = get_chat_model(chat_config.provider, chat_config.name)
        
        try:
            response = await call_llm(
                system="You are an expert architect.",
                model=chat_model,
                message=prompt
            )
            raw = str(response)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception as e:
            logger.warning(f"AI Analysis failed: {e}")
            
        return {
            "refactoring_summary": "Manual review recommended for deep refactoring strategy.",
            "mermaid_diagram": "graph TD\n  Root --> Modules"
        }

    def _format_markdown_report(self, structure: Dict, security: List, perf: List, large: List, expert: Dict) -> str:
        report = f"# Codebase Audit Report: {os.path.basename(self.path)}\n\n"
        
        # 1. Architectural Overview
        report += "## Architectural Overview\n"
        report += f"**Core Modules**: {', '.join(structure['modules']) if structure['modules'] else 'N/A'}\n"
        report += f"**Entry Points**: {', '.join(structure['entry_points']) if structure['entry_points'] else 'None detected'}\n\n"
        report += "```mermaid\n"
        report += expert.get("mermaid_diagram", "graph TD\n  Root --> Modules") + "\n"
        report += "```\n\n"
        
        # 2. AI Strategy
        report += "## AI Refactoring Strategy\n"
        report += f"{expert.get('refactoring_summary', 'N/A')}\n\n"
        
        # 3. Security
        report += "## Security Vulnerabilities\n"
        if not security:
            report += "✅ No obvious secrets or vulnerabilities detected by heuristic scan.\n\n"
        else:
            for s in security:
                report += f"- 🚩 **{s['type']}**: detected in `{s['file']}`\n"
            report += "\n"
            
        # 4. Performance
        report += "## Performance Insights\n"
        if not perf and not large:
            report += "✅ No obvious bottlenecks detected.\n\n"
        else:
            for p in perf:
                report += f"- 🐢 **{p['type']}**: detected in `{p['file']}`\n"
            for l in large:
                report += f"- 📂 **Large File**: `{l['file']}` ({l['size']} MB)\n"
            report += "\n"
            
        return report

    async def _post_to_remote(self, report: str) -> str:
        """Posts the report as an issue to the remote repository."""
        try:
            from python.tools.repo_automation.providers import load_forgejo_credentials, load_github_credentials
            
            repo_name = os.path.basename(self.path)
            title = f"Codebase Audit Report: {repo_name}"
            
            if self.repo_type == "forgejo":
                creds = load_forgejo_credentials(self.agent.context, self.args)
                if not creds or not creds.is_complete():
                    return "Skipped issue posting: Forgejo credentials incomplete."
                result = await create_issue_forgejo(creds, title, report)
            else:
                creds = load_github_credentials(self.agent.context, self.args)
                if not creds or not creds.is_complete():
                    return "Skipped issue posting: GitHub credentials incomplete."
                result = await create_issue_github(creds, title, report)
                
            if result.get("success"):
                return f"Issue created: #{result.get('issue_number')}"
            return f"Failed to post issue: {result.get('message')}"
            
        except Exception as e:
            return f"Error during issue posting: {e}"
