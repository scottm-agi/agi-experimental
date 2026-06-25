from __future__ import annotations
import os
import sys

# Fix sys.path when run as CLI script (python3 borg_compare.py ...)
# Must happen before relative imports like 'from python.helpers.tool import Tool'
if __name__ == "__main__":
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(os.path.dirname(_script_dir))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

import ast
import re
import json
import subprocess
import logging
import textwrap
from collections import Counter, defaultdict
from typing import Dict, Any, List, Optional, Set, Tuple

# Conditional imports: when run as CLI (__main__), avoid importing the full
# agent framework (Tool → Agent → models → nest_asyncio chain).
# Instead, provide lightweight stubs so the class definition works standalone.
if __name__ == "__main__":
    class _CliResponse:
        def __init__(self, message="", break_loop=False, **kw):
            self.message = message
            self.break_loop = break_loop
    class _CliTool:
        def __init__(self, *args, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
            self.progress = ""
    Tool = _CliTool
    Response = _CliResponse
else:
    from python.helpers.tool import Tool, Response
    from python.helpers import files
    from python.helpers.file_guard import FileGuard

from typing import TYPE_CHECKING
from python.helpers.borg_forensics import BorgForensics
from python.helpers.borg_reports import BorgReportWriter
from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("borg-compare")


class BorgCompare(Tool):
    """
    Comprehensive codebase analysis and pair-wise comparison tool ("Borg").

    Uses ripgrep, file system analysis, and dependency parsing to deeply
    dissect codebases and produce a comprehensive markdown report suitable
    for porting / assimilation decisions.
    """

    # DUP-3: Uses shared DEFAULT_PROJECT_SKIP_DIRS + borg-specific extras.
    EXCLUDE_DIRS = set(DEFAULT_PROJECT_SKIP_DIRS) | {
        ".gemini", ".venv-test", ".pytest_cache", ".smart-coding-cache",
        ".roo", "logs", "delete", ".agix.proj", ".tox", ".mypy_cache",
    }

    # Source file extensions by language family
    LANG_EXTENSIONS = {
        "python": {".py"},
        "javascript": {".js", ".jsx", ".mjs", ".cjs"},
        "typescript": {".ts", ".tsx"},
        "go": {".go"},
        "rust": {".rs"},
        "shell": {".sh", ".bash"},
        "html": {".html", ".htm"},
        "css": {".css", ".scss", ".sass", ".less"},
        "json": {".json"},
        "yaml": {".yml", ".yaml"},
        "markdown": {".md"},
    }

    # Architecture pattern queries for ripgrep
    ARCH_PATTERNS = {
        "class_definitions": r"class\s+\w+",
        "async_patterns": r"async\s+(def|function)",
        "decorators": r"@\w+",
        "dependency_injection": r"(inject|@Injectable|container|resolve|provider)",
        "event_patterns": r"(EventEmitter|addEventListener|on\(|emit\()",
        "api_routes": r"(app\.(get|post|put|delete|patch)|@router\.|@app\.route)",
        "error_handling": r"(try|catch|except|finally)",
        "logging": r"(logger\.|logging\.|console\.(log|error|warn))",
        "testing": r"(def test_|it\(|describe\(|@pytest|assert )",
    }

    # Framework fingerprints — detect specific frameworks via distinctive imports/patterns
    FRAMEWORK_FINGERPRINTS = {
        "FastAPI": r"from fastapi|import fastapi",
        "Flask": r"from flask|import flask",
        "Django": r"from django|import django",
        "Express": r"require\(['\"]express['\"]\)",
        "React": r"from ['\"]react['\"]|import React",
        "Vue": r"from ['\"]vue['\"]|createApp",
        "SQLAlchemy": r"from sqlalchemy|import sqlalchemy",
        "Pydantic": r"from pydantic|import pydantic",
        "Celery": r"from celery|import celery",
        "Redis": r"import redis|from redis",
        "asyncio": r"import asyncio|from asyncio",
        "WebSocket": r"websocket|WebSocket|ws://",
        "gRPC": r"import grpc|from grpc",
        "pytest": r"import pytest|from pytest",
        "Docker": r"FROM .+\nRUN|ENTRYPOINT|CMD \[",
    }

    # Debt markers
    DEBT_PATTERNS = {
        "TODO": r"\bTODO\b",
        "FIXME": r"\bFIXME\b",
        "HACK": r"\bHACK\b",
        "XXX": r"\bXXX\b",
    }

    # Gitingest default cap (chars ≈ 500k words × 5 chars/word)
    GITINGEST_MAX_CHARS = 2_500_000

    def __init__(self, agent: Agent, name: str, method: str | None, args: dict, message: str, loop_data: LoopData | None, **kwargs):
        super().__init__(agent, name, method, args, message, loop_data, **kwargs)
        self.source_path = args.get("source_path", "")
        self.target_path = args.get("target_path", "")
        self.action = args.get("action", "compare")  # "analyze" or "compare"
        self.output_dir = args.get("output_dir", "")
        self.report_depth = args.get("report_depth", "comprehensive")  # "comprehensive" or "summary"

    async def execute(self, **kwargs) -> Response:
        # ── FileGuard: Validate output_dir before any writes ──
        if self.output_dir:
            from python.helpers import projects
            active_project = projects.get_context_project_name(self.agent.context) if hasattr(self, 'agent') and self.agent else None
            test_path = os.path.join(self.output_dir, "_fileguard_check")
            is_allowed, guard_msg = FileGuard.validate_write_path(test_path, active_project)
            if not is_allowed:
                return Response(message=f"FileGuard: {guard_msg}", break_loop=False)
            if guard_msg.startswith("AUTO_RESOLVED:"):
                resolved = guard_msg.split("AUTO_RESOLVED:")[1]
                self.output_dir = os.path.dirname(resolved)

        if self.action == "analyze":
            if not self.source_path or not os.path.exists(self.source_path):
                return Response(message=f"Error: source_path '{self.source_path}' does not exist.", break_loop=False)

            analysis = self._deep_analysis(self.source_path)

            if self.report_depth == "comprehensive" and self.output_dir:
                manifest = self._write_comprehensive_report(analysis)
                next_steps = self._build_phase2_instructions()
                return Response(
                    message=f"Comprehensive forensic analysis complete.\n\n{manifest}\n{next_steps}",
                    break_loop=False,
                )
            else:
                report = self._format_analysis_report(analysis)
                if self.output_dir:
                    os.makedirs(self.output_dir, exist_ok=True)
                    report_path = os.path.join(self.output_dir, "analysis_report.md")
                    with open(report_path, "w") as f:
                        f.write(report)
                    return Response(
                        message=f"Analysis report saved to `{report_path}`.\n\n{report[:2000]}...\n\n(Full report: {len(report)} chars)",
                        break_loop=False,
                    )
                return Response(message=report, break_loop=False)

        elif self.action == "compare":
            if not self.source_path or not os.path.exists(self.source_path):
                return Response(message=f"Error: source_path '{self.source_path}' does not exist.", break_loop=False)
            if not self.target_path or not os.path.exists(self.target_path):
                return Response(message=f"Error: target_path '{self.target_path}' does not exist.", break_loop=False)

            analysis_source = self._deep_analysis(self.source_path)
            analysis_target = self._deep_analysis(self.target_path)
            comparison = self._compare_pair(analysis_source, analysis_target)

            if self.report_depth == "comprehensive" and self.output_dir:
                manifest_src = self._write_comprehensive_report(analysis_source, prefix="source_")
                manifest_tgt = self._write_comprehensive_report(analysis_target, prefix="target_")
                comparison_report = self._format_comparison_report(analysis_source, analysis_target, comparison)
                os.makedirs(self.output_dir, exist_ok=True)
                comp_path = os.path.join(self.output_dir, "08_strategic_comparison.md")
                with open(comp_path, "w") as f:
                    f.write(comparison_report)
                # Write index
                index = self._write_comparison_index(analysis_source, analysis_target, manifest_src, manifest_tgt, comp_path)
                next_steps = self._build_phase2_instructions()
                return Response(
                    message=f"Comprehensive forensic comparison complete.\n\n{index}\n{next_steps}",
                    break_loop=False,
                )
            else:
                report = self._format_comparison_report(analysis_source, analysis_target, comparison)
                if self.output_dir:
                    os.makedirs(self.output_dir, exist_ok=True)
                    report_path = os.path.join(self.output_dir, "borg_comparison_report.md")
                    with open(report_path, "w") as f:
                        f.write(report)
                    return Response(
                        message=f"Comparison report saved to `{report_path}`.\n\n{report[:2000]}...\n\n(Full report: {len(report)} chars)",
                        break_loop=False,
                    )
                return Response(message=report, break_loop=False)

        return Response(message=f"Unknown action: {self.action}. Use 'analyze' or 'compare'.", break_loop=False)

    def _build_phase2_instructions(self) -> str:
        """Build Phase 2 next-steps instructions for the calling agent."""
        return (
            "\n## ⚠️ PHASE 1 COMPLETE — PHASE 2 REQUIRED\n"
            "Static forensic analysis is complete. You MUST now perform LLM-powered semantic analysis:\n\n"
            f"1. **Read each sub-report**: `cat {self.output_dir}/*.md`\n"
            f"2. **Read ingest dumps in 5K-line chunks**:\n"
            f"   ```bash\n"
            f"   sed -n '1,5000p' {self.output_dir}/*_ingest.txt\n"
            f"   sed -n '5001,10000p' {self.output_dir}/*_ingest.txt\n"
            f"   # Continue until EOF\n"
            f"   ```\n"
            f"3. **For each chunk**, identify: features, patterns, integration points, quality narratives\n"
            f"4. **Write semantic analysis** to: `{self.output_dir}/09_llm_semantic_analysis.md`\n"
            f"5. **Write feature inventory** to: `{self.output_dir}/10_feature_inventory.md`\n"
            f"6. **Continue** until all ingest chunks are processed\n\n"
            f"> The ingest files exist specifically to be consumed by the LLM for deep semantic understanding.\n"
            f"> The static reports are just structured data — the real value is in LLM-powered narrative synthesis.\n"
        )

    # ── Phase 2: Deep Analysis ──

    def _deep_analysis(self, root: str) -> Dict[str, Any]:
        """Perform comprehensive forensic analysis of a single codebase (21 dimensions)."""
        # Core analysis phases (15 original dimensions)
        result = {
            "name": os.path.basename(root),
            "root": root,
            "structure": self._scan_structure(root),
            "metrics": self._compute_metrics(root),
            "dependencies": self._extract_dependencies(root),
            "architecture": self._detect_architecture_patterns(root),
            "debt": self._count_debt_markers(root),
            "entry_points": self._find_entry_points(root),
            "config_files": self._find_config_files(root),
            "ast": self._ast_analysis(root),
            "deep_ripgrep": self._deep_ripgrep_with_context(root),
            "hotspots": self._complexity_hotspots(root),
            "gitingest": self._gitingest_dump(root),
            "design_patterns": self._detect_design_patterns(root),
            "call_graph": self._call_graph_analysis(root),
            "api_surface": self._api_surface_detection(root),
        }
        # 6 new gold-standard forensic dimensions
        result["cyclomatic_complexity"] = self._cyclomatic_complexity(root)
        result["comment_density"] = self._comment_density(root)
        result["duplication"] = self._duplication_detection(root)
        result["security"] = self._security_markers(root)
        result["test_analysis"] = self._test_analysis(root)
        result["module_coupling"] = self._module_coupling(root)
        # Architecture classification needs collected data
        result["architecture_style"] = self._classify_architecture(root, result)
        return result

    def _scan_structure(self, root: str) -> Dict[str, Any]:
        """Scan directory structure and file inventory."""
        dirs = []
        file_counts: Dict[str, int] = {}
        total_files = 0

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.EXCLUDE_DIRS]
            rel = os.path.relpath(dirpath, root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth > 4:
                del dirnames[:]
                continue

            if depth <= 2:
                dirs.append(rel if rel != "." else "/")

            for f in filenames:
                ext = os.path.splitext(f)[1].lower()
                file_counts[ext] = file_counts.get(ext, 0) + 1
                total_files += 1

        return {
            "directories": dirs[:80],
            "file_counts_by_ext": dict(sorted(file_counts.items(), key=lambda x: -x[1])[:20]),
            "total_files": total_files,
        }

    def _compute_metrics(self, root: str) -> Dict[str, Any]:
        """Compute lines of code per language family."""
        loc_by_lang: Dict[str, int] = {}
        source_file_count = 0

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.EXCLUDE_DIRS]
            for f in filenames:
                ext = os.path.splitext(f)[1].lower()
                lang = None
                for lang_name, exts in self.LANG_EXTENSIONS.items():
                    if ext in exts:
                        lang = lang_name
                        break
                if not lang:
                    continue

                filepath = os.path.join(dirpath, f)
                try:
                    with open(filepath, "r", errors="ignore") as fh:
                        line_count = sum(1 for _ in fh)
                    loc_by_lang[lang] = loc_by_lang.get(lang, 0) + line_count
                    source_file_count += 1
                except Exception:
                    continue

        return {
            "lines_of_code": loc_by_lang,
            "total_loc": sum(loc_by_lang.values()),
            "source_files": source_file_count,
        }

    def _extract_dependencies(self, root: str) -> Dict[str, Any]:
        """Extract dependency info from package manifests."""
        deps: Dict[str, Any] = {}

        # Python: requirements.txt
        req_path = os.path.join(root, "requirements.txt")
        if os.path.exists(req_path):
            try:
                content = files.read_file(req_path)
                py_deps = [line.strip().split("==")[0].split(">=")[0].split("<=")[0]
                           for line in content.splitlines()
                           if line.strip() and not line.startswith("#")]
                deps["python"] = py_deps
            except Exception:
                pass

        # Node.js: package.json
        pkg_path = os.path.join(root, "package.json")
        if os.path.exists(pkg_path):
            try:
                pkg = json.loads(files.read_file(pkg_path))
                deps["npm_deps"] = list(pkg.get("dependencies", {}).keys())
                deps["npm_dev_deps"] = list(pkg.get("devDependencies", {}).keys())
            except Exception:
                pass

        # Go: go.mod
        gomod_path = os.path.join(root, "go.mod")
        if os.path.exists(gomod_path):
            try:
                content = files.read_file(gomod_path)
                go_deps = re.findall(r"^\s+(\S+)\s+v", content, re.MULTILINE)
                deps["go"] = go_deps
            except Exception:
                pass

        # Rust: Cargo.toml
        cargo_path = os.path.join(root, "Cargo.toml")
        if os.path.exists(cargo_path):
            try:
                content = files.read_file(cargo_path)
                rust_deps = re.findall(r'^\s*(\w[\w-]*)\s*=', content, re.MULTILINE)
                deps["rust"] = rust_deps
            except Exception:
                pass

        return deps

    def _detect_architecture_patterns(self, root: str) -> Dict[str, int]:
        """Use ripgrep to detect architecture patterns."""
        results = {}
        for pattern_name, pattern in self.ARCH_PATTERNS.items():
            count = self._run_ripgrep_count(root, pattern)
            results[pattern_name] = count
        return results

    def _count_debt_markers(self, root: str) -> Dict[str, int]:
        """Count technical debt markers using ripgrep."""
        results = {}
        for name, pattern in self.DEBT_PATTERNS.items():
            count = self._run_ripgrep_count(root, pattern)
            results[name] = count
        return results

    def _find_entry_points(self, root: str) -> List[str]:
        """Find likely entry points."""
        patterns = [
            r"main\.py$", r"app\.py$", r"server\.py$", r"run_.*\.py$",
            r"index\.(js|ts)$", r"start\.sh$", r"manage\.py$", r"wsgi\.py$",
        ]
        entries = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.EXCLUDE_DIRS]
            for f in filenames:
                if any(re.search(p, f) for p in patterns):
                    entries.append(os.path.relpath(os.path.join(dirpath, f), root))
        return entries[:20]

    def _find_config_files(self, root: str) -> List[str]:
        """Find configuration files."""
        config_names = {
            "package.json", "tsconfig.json", "pyproject.toml", "setup.py",
            "Cargo.toml", "go.mod", "Dockerfile", "docker-compose.yml",
            "docker-compose.yaml", ".env.example", "requirements.txt",
            "Makefile", "webpack.config.js", "vite.config.ts", "vite.config.js",
        }
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.EXCLUDE_DIRS]
            depth = os.path.relpath(dirpath, root).count(os.sep)
            if depth > 2:
                del dirnames[:]
                continue
            for f in filenames:
                if f in config_names:
                    found.append(os.path.relpath(os.path.join(dirpath, f), root))
        return found[:30]

    # ── Phase 2b: AST Deep Analysis (Python) ──

    def _ast_analysis(self, root: str) -> Dict[str, Any]:
        """Parse Python files with AST for class hierarchy, imports, function signatures."""
        classes: List[Dict[str, Any]] = []
        imports: Dict[str, List[str]] = defaultdict(list)  # module -> [importing_file]
        functions: List[Dict[str, Any]] = []
        all_py_files = []

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.EXCLUDE_DIRS]
            for f in filenames:
                if not f.endswith(".py"):
                    continue
                filepath = os.path.join(dirpath, f)
                rel = os.path.relpath(filepath, root)
                all_py_files.append(rel)
                try:
                    with open(filepath, "r", errors="ignore") as fh:
                        source = fh.read()
                    tree = ast.parse(source, filename=rel)
                except (SyntaxError, UnicodeDecodeError, Exception):
                    continue

                for node in ast.walk(tree):
                    # Class hierarchy
                    if isinstance(node, ast.ClassDef):
                        bases = []
                        for b in node.bases:
                            if isinstance(b, ast.Name):
                                bases.append(b.id)
                            elif isinstance(b, ast.Attribute):
                                bases.append(ast.dump(b))
                        methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                        decorators = []
                        for d in node.decorator_list:
                            if isinstance(d, ast.Name):
                                decorators.append(d.id)
                            elif isinstance(d, ast.Attribute):
                                decorators.append(d.attr)
                        classes.append({
                            "name": node.name,
                            "file": rel,
                            "bases": bases,
                            "methods": methods,
                            "method_count": len(methods),
                            "decorators": decorators,
                            "line": node.lineno,
                        })

                    # Top-level functions
                    if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                        # Only capture module-level or class-level signatures
                        args = []
                        for a in node.args.args:
                            args.append(a.arg)
                        is_async = isinstance(node, ast.AsyncFunctionDef)
                        functions.append({
                            "name": node.name,
                            "file": rel,
                            "args": args[:8],  # cap display
                            "is_async": is_async,
                            "line": node.lineno,
                        })

                    # Import graph
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports[alias.name].append(rel)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports[node.module].append(rel)

        # Build import frequency ranking
        import_freq = {mod: len(set(files)) for mod, files in imports.items()}
        top_imports = sorted(import_freq.items(), key=lambda x: -x[1])[:30]

        # Inheritance tree: map base -> children
        inheritance: Dict[str, List[str]] = defaultdict(list)
        for cls in classes:
            for base in cls["bases"]:
                inheritance[base].append(cls["name"])

        return {
            "total_python_files": len(all_py_files),
            "total_classes": len(classes),
            "total_functions": len(functions),
            "classes": sorted(classes, key=lambda x: -x["method_count"])[:50],
            "top_imports": top_imports,
            "inheritance_tree": dict(inheritance),
            "async_function_count": sum(1 for f in functions if f.get("is_async")),
            "largest_classes": sorted(classes, key=lambda x: -x["method_count"])[:10],
        }

    # ── Phase 2c: Deep Ripgrep with Context ──

    def _deep_ripgrep_with_context(self, root: str) -> Dict[str, Any]:
        """Extended ripgrep: file-level hits, hotspot detection, framework fingerprinting."""
        # Pattern hits per file (hotspot detection)
        file_hit_counts: Counter = Counter()
        pattern_files: Dict[str, List[str]] = {}

        for pattern_name, pattern in self.ARCH_PATTERNS.items():
            matched_files = self._run_ripgrep_files(root, pattern, max_files=50)
            pattern_files[pattern_name] = matched_files
            for f in matched_files:
                file_hit_counts[f] += 1

        # Hotspots: files that match the most different patterns
        hotspots = file_hit_counts.most_common(15)

        # Framework fingerprinting
        detected_frameworks: Dict[str, int] = {}
        for fw_name, fw_pattern in self.FRAMEWORK_FINGERPRINTS.items():
            count = self._run_ripgrep_count(root, fw_pattern)
            if count > 0:
                detected_frameworks[fw_name] = count

        return {
            "hotspots": [{"file": f, "pattern_hits": c} for f, c in hotspots],
            "pattern_file_map": {k: v[:10] for k, v in pattern_files.items()},
            "detected_frameworks": detected_frameworks,
        }

    # ── Phase 2d: Complexity Hotspots ──

    def _complexity_hotspots(self, root: str) -> Dict[str, Any]:
        """Find complexity centers: largest files, most classes, deepest nesting."""
        file_sizes: List[Tuple[str, int]] = []

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.EXCLUDE_DIRS]
            for f in filenames:
                ext = os.path.splitext(f)[1].lower()
                if ext not in (".py", ".js", ".ts", ".go", ".rs", ".jsx", ".tsx"):
                    continue
                filepath = os.path.join(dirpath, f)
                try:
                    with open(filepath, "r", errors="ignore") as fh:
                        loc = sum(1 for _ in fh)
                    file_sizes.append((os.path.relpath(filepath, root), loc))
                except Exception:
                    continue

        file_sizes.sort(key=lambda x: -x[1])
        return {
            "largest_files": [{"file": f, "loc": loc} for f, loc in file_sizes[:15]],
            "files_over_500_loc": sum(1 for _, loc in file_sizes if loc > 500),
            "files_over_1000_loc": sum(1 for _, loc in file_sizes if loc > 1000),
            "median_file_loc": file_sizes[len(file_sizes) // 2][1] if file_sizes else 0,
        }

    # ── Phase 2e: Gitingest-style Full-Text Dump ──

    def _gitingest_dump(self, root: str) -> Dict[str, Any]:
        """Create a concatenated source dump (like gitingest) capped at ~500k words."""
        chunks: List[str] = []
        total_chars = 0
        files_included = 0
        files_skipped = 0
        source_exts = set()
        for exts in self.LANG_EXTENSIONS.values():
            source_exts.update(exts)

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.EXCLUDE_DIRS]
            for f in sorted(filenames):
                ext = os.path.splitext(f)[1].lower()
                if ext not in source_exts:
                    continue
                filepath = os.path.join(dirpath, f)
                rel = os.path.relpath(filepath, root)
                try:
                    with open(filepath, "r", errors="ignore") as fh:
                        content = fh.read()
                except Exception:
                    continue

                file_header = f"\n{'='*60}\n# FILE: {rel}\n{'='*60}\n"
                file_block = file_header + content + "\n"

                if total_chars + len(file_block) > self.GITINGEST_MAX_CHARS:
                    files_skipped += 1
                    continue

                chunks.append(file_block)
                total_chars += len(file_block)
                files_included += 1

        dump_text = "".join(chunks)
        # Save to output directory if specified
        dump_path = ""
        if self.output_dir:
            os.makedirs(self.output_dir, exist_ok=True)
            dump_path = os.path.join(self.output_dir, f"{os.path.basename(root)}_ingest.txt")
            os.makedirs(os.path.dirname(dump_path), exist_ok=True)
            with open(dump_path, "w") as f:
                f.write(dump_text)

        return {
            "total_chars": total_chars,
            "estimated_words": total_chars // 5,
            "files_included": files_included,
            "files_skipped_cap": files_skipped,
            "dump_path": dump_path,
        }

    # ── Phase 2f: Design Pattern Detection ──

    def _detect_design_patterns(self, root: str) -> Dict[str, List[str]]:
        """Detect common software design patterns via AST + ripgrep heuristics."""
        patterns_found: Dict[str, List[str]] = {}

        # Singleton: class with _instance or __new__ returning cls
        singleton_files = self._run_ripgrep_files(root, r"_instance\s*=|__new__\s*\(cls", max_files=20)
        if singleton_files:
            patterns_found["Singleton"] = singleton_files

        # Factory: functions/classes named *Factory* or *create_*/*build_*
        factory_files = self._run_ripgrep_files(root, r"(class \w*Factory|def (create_|build_|make_)\w+)", max_files=20)
        if factory_files:
            patterns_found["Factory"] = factory_files

        # Observer/Event: emit/subscribe/on_event/listener patterns
        observer_files = self._run_ripgrep_files(root, r"(\.emit\(|\.subscribe\(|\.on\(|add_listener|EventEmitter|Signal)", max_files=20)
        if observer_files:
            patterns_found["Observer/Event"] = observer_files

        # Strategy: classes with execute/run method + injection
        strategy_files = self._run_ripgrep_files(root, r"(class \w*Strategy|class \w*Policy|def execute\(self)", max_files=20)
        if strategy_files:
            patterns_found["Strategy"] = strategy_files

        # Decorator: @decorator or wrapper functions
        decorator_files = self._run_ripgrep_files(root, r"(def \w+\(func\)|functools\.wraps|@\w+\ndef )", max_files=20)
        if decorator_files:
            patterns_found["Decorator"] = decorator_files

        # Plugin/Extension: register/plugin/hook patterns
        plugin_files = self._run_ripgrep_files(root, r"(register_plugin|\.register\(|class \w*Extension|class \w*Plugin|hook_)", max_files=20)
        if plugin_files:
            patterns_found["Plugin/Extension"] = plugin_files

        # Middleware: middleware/before_request/after_request
        middleware_files = self._run_ripgrep_files(root, r"(middleware|before_request|after_request|app\.use\()", max_files=20)
        if middleware_files:
            patterns_found["Middleware"] = middleware_files

        return patterns_found

    # ── Phase 2g: Call Graph (AST-based) ──

    def _call_graph_analysis(self, root: str) -> Dict[str, Any]:
        """Build a lightweight call graph from Python AST: who calls whom."""
        # Map function_name -> [files where it's called]
        call_counts: Counter = Counter()
        defined_funcs: Dict[str, str] = {}  # func_name -> defining_file

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.EXCLUDE_DIRS]
            for f in filenames:
                if not f.endswith(".py"):
                    continue
                filepath = os.path.join(dirpath, f)
                rel = os.path.relpath(filepath, root)
                try:
                    with open(filepath, "r", errors="ignore") as fh:
                        source = fh.read()
                    tree = ast.parse(source, filename=rel)
                except Exception:
                    continue

                for node in ast.walk(tree):
                    # Track definitions
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defined_funcs[node.name] = rel
                    # Track calls
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name):
                            call_counts[node.func.id] += 1
                        elif isinstance(node.func, ast.Attribute):
                            call_counts[node.func.attr] += 1

        # Most-called functions
        top_called = call_counts.most_common(25)
        return {
            "total_unique_calls": len(call_counts),
            "most_called_functions": [
                {"name": name, "call_count": count, "defined_in": defined_funcs.get(name, "external/builtin")}
                for name, count in top_called
            ],
        }

    # ── Phase 2h: API Surface Detection ──

    def _api_surface_detection(self, root: str) -> Dict[str, Any]:
        """Detect public API surfaces: HTTP endpoints, CLI commands, tool registrations."""
        endpoints: List[Dict[str, str]] = []
        cli_commands: List[str] = []

        # HTTP endpoints via ripgrep
        for method in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
            pattern = rf'@(app|router|api)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)'
            matched = self._run_ripgrep_files(root, pattern, max_files=30)
            if matched:
                for f in matched:
                    endpoints.append({"method": method, "file": f})

        # FastAPI/Flask route decorators
        route_files = self._run_ripgrep_files(root, r'@(app|router)\.\w+\(\s*["\'/]', max_files=30)

        # argparse / click CLI detection
        cli_files = self._run_ripgrep_files(root, r"(argparse\.ArgumentParser|@click\.command|@click\.group|add_argument\()", max_files=20)
        if cli_files:
            cli_commands = cli_files

        # Tool registrations (agi-experimental pattern)
        tool_files = self._run_ripgrep_files(root, r"(class \w+\(Tool\)|class \w+Tool\(|register_tool)", max_files=20)

        return {
            "http_route_files": route_files[:15],
            "cli_files": cli_commands[:10],
            "tool_registrations": tool_files[:15],
            "total_api_surface": len(route_files) + len(cli_commands) + len(tool_files),
        }

    # ── Phase 2i: Architecture Style Classification ──

    def _classify_architecture(self, root: str, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Classify the overall architecture style based on collected evidence."""
        indicators: Dict[str, int] = defaultdict(int)

        arch = analysis.get("architecture", {})
        deps = analysis.get("dependencies", {})
        structure = analysis.get("structure", {})

        # Microservices indicators
        docker_count = self._run_ripgrep_count(root, r"(Dockerfile|docker-compose|FROM .+)")
        if docker_count > 2:
            indicators["microservices"] += docker_count
        if arch.get("api_endpoints", 0) > 5:
            indicators["microservices"] += 2

        # Event-driven indicators
        event_count = arch.get("event_driven", 0) + arch.get("async_patterns", 0)
        if event_count > 5:
            indicators["event_driven"] += event_count

        # Layered indicators (presence of typical layers)
        dirs = set(structure.get("directories", []))
        layer_dirs = {"models", "views", "controllers", "services", "repositories", "api", "core", "helpers", "utils"}
        layer_overlap = dirs & layer_dirs
        if len(layer_overlap) >= 3:
            indicators["layered"] += len(layer_overlap) * 2

        # Plugin/extension-based
        plugin_count = arch.get("plugins", 0)
        if plugin_count > 3:
            indicators["plugin_based"] += plugin_count

        # MVC
        if {"models", "views", "controllers"} <= dirs or {"models", "templates", "views"} <= dirs:
            indicators["mvc"] += 5

        # Monolith (large single entry, few services)
        if len(analysis.get("entry_points", [])) <= 2 and analysis["metrics"]["total_loc"] > 10000:
            indicators["monolith"] += 3

        top_style = max(indicators.items(), key=lambda x: x[1]) if indicators else ("unknown", 0)

        return {
            "primary_style": top_style[0],
            "confidence_score": top_style[1],
            "all_indicators": dict(indicators),
        }

    # ── Phase 2j-2o: Gold-Standard Forensic Dimensions (delegated to BorgForensics) ──

    def _get_forensics(self) -> BorgForensics:
        """Lazy-init shared BorgForensics instance."""
        if not hasattr(self, "_forensics"):
            self._forensics = BorgForensics()
        return self._forensics

    def _cyclomatic_complexity(self, root: str) -> Dict[str, Any]:
        """Delegate to BorgForensics helper."""
        return self._get_forensics().cyclomatic_complexity(root)

    def _comment_density(self, root: str) -> Dict[str, Any]:
        """Delegate to BorgForensics helper."""
        return self._get_forensics().comment_density(root)

    def _duplication_detection(self, root: str, block_size: int = 5) -> Dict[str, Any]:
        """Delegate to BorgForensics helper."""
        return self._get_forensics().duplication_detection(root, block_size)

    def _security_markers(self, root: str) -> Dict[str, Any]:
        """Delegate to BorgForensics helper."""
        return self._get_forensics().security_markers(root)

    def _test_analysis(self, root: str) -> Dict[str, Any]:
        """Delegate to BorgForensics helper."""
        return self._get_forensics().test_analysis(root)

    def _module_coupling(self, root: str) -> Dict[str, Any]:
        """Delegate to BorgForensics helper."""
        return self._get_forensics().module_coupling(root)

    # ── Ripgrep Helpers ──

    def _run_ripgrep_count(self, root: str, pattern: str) -> int:
        """Run `rg -c` and return total match count."""
        try:
            result = subprocess.run(
                ["rg", "-c", "--no-filename", "-e", pattern, root,
                 "--glob", "!.git", "--glob", "!node_modules",
                 "--glob", "!__pycache__", "--glob", "!dist",
                 "--glob", "!build", "--glob", "!*.min.js"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode > 1:
                return 0
            total = 0
            for line in result.stdout.strip().splitlines():
                try:
                    total += int(line.strip())
                except ValueError:
                    continue
            return total
        except Exception:
            return 0

    def _run_ripgrep_files(self, root: str, pattern: str, max_files: int = 20) -> List[str]:
        """Run `rg -l` and return matching file paths (relative)."""
        try:
            result = subprocess.run(
                ["rg", "-l", "-e", pattern, root,
                 "--glob", "!.git", "--glob", "!node_modules",
                 "--glob", "!__pycache__"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode > 1:
                return []
            matched = []
            for line in result.stdout.strip().splitlines():
                rel = os.path.relpath(line.strip(), root)
                matched.append(rel)
                if len(matched) >= max_files:
                    break
            return matched
        except Exception:
            return []

    # ── Phase 3: Pair-wise Comparison ──

    def _compare_pair(self, source: Dict, target: Dict) -> Dict[str, Any]:
        """Compare two codebase analyses pair-wise."""
        comparison: Dict[str, Any] = {}

        # Directory overlap
        src_dirs = set(source["structure"]["directories"])
        tgt_dirs = set(target["structure"]["directories"])
        comparison["common_dirs"] = sorted(src_dirs & tgt_dirs)
        comparison["source_only_dirs"] = sorted(src_dirs - tgt_dirs)
        comparison["target_only_dirs"] = sorted(tgt_dirs - src_dirs)

        # Dependency overlap
        all_src_deps = self._flatten_deps(source["dependencies"])
        all_tgt_deps = self._flatten_deps(target["dependencies"])
        comparison["shared_deps"] = sorted(all_src_deps & all_tgt_deps)
        comparison["source_only_deps"] = sorted(all_src_deps - all_tgt_deps)
        comparison["target_only_deps"] = sorted(all_tgt_deps - all_src_deps)

        # Architecture pattern delta
        arch_delta = {}
        all_patterns = set(source["architecture"].keys()) | set(target["architecture"].keys())
        for p in all_patterns:
            s = source["architecture"].get(p, 0)
            t = target["architecture"].get(p, 0)
            arch_delta[p] = {"source": s, "target": t, "delta": t - s}
        comparison["architecture_delta"] = arch_delta

        # Metrics comparison
        comparison["metrics_source"] = source["metrics"]
        comparison["metrics_target"] = target["metrics"]

        return comparison

    def _flatten_deps(self, deps: Dict[str, Any]) -> Set[str]:
        """Flatten all dependency lists into a single set."""
        all_deps: Set[str] = set()
        for key, val in deps.items():
            if isinstance(val, list):
                all_deps.update(val)
        return all_deps

    # ── Phase 4: Report Generation ──

    # ── Phase 4a: Comprehensive Multi-File Report (delegated to BorgReportWriter) ──

    def _write_comprehensive_report(self, analysis: Dict, prefix: str = "") -> str:
        """Delegate to BorgReportWriter helper. Returns manifest text."""
        writer = BorgReportWriter(self.output_dir)
        return writer.write_comprehensive_report(analysis, prefix)

    def _write_comparison_index(self, source_analysis: Dict, target_analysis: Dict,
                                 manifest_src: str, manifest_tgt: str,
                                 comp_path: str) -> str:
        """Delegate to BorgReportWriter helper. Returns index content."""
        writer = BorgReportWriter(self.output_dir)
        return writer.write_comparison_index(source_analysis, target_analysis,
                                              manifest_src, manifest_tgt, comp_path)


    def _analyze_single(self, root: str) -> str:
        """Generate a single codebase analysis report."""
        analysis = self._deep_analysis(root)
        return self._format_analysis_report(analysis)

    def _format_analysis_report(self, analysis: Dict) -> str:
        """Format a single codebase analysis as comprehensive forensic markdown."""
        a = analysis
        lines = [f"# Codebase Forensic Analysis: {a['name']}\n"]

        # Structure
        lines.append("## Project Structure")
        lines.append(f"- **Total Files**: {a['structure']['total_files']}")
        lines.append(f"- **Top Directories**: {', '.join(a['structure']['directories'][:15])}")
        lines.append("\n### File Types")
        for ext, count in list(a["structure"]["file_counts_by_ext"].items())[:15]:
            lines.append(f"- `{ext}`: {count} files")

        # Metrics
        lines.append("\n## Code Metrics")
        lines.append(f"- **Total LOC**: {a['metrics']['total_loc']:,}")
        lines.append(f"- **Source Files**: {a['metrics']['source_files']}")
        for lang, loc in a["metrics"]["lines_of_code"].items():
            lines.append(f"  - {lang}: {loc:,} lines")

        # AST Analysis
        ast_data = a.get("ast", {})
        if ast_data:
            lines.append("\n## AST Analysis (Python)")
            lines.append(f"- **Python Files Parsed**: {ast_data.get('total_python_files', 0)}")
            lines.append(f"- **Total Classes**: {ast_data.get('total_classes', 0)}")
            lines.append(f"- **Total Functions**: {ast_data.get('total_functions', 0)}")
            lines.append(f"- **Async Functions**: {ast_data.get('async_function_count', 0)}")

            # Class hierarchy
            if ast_data.get("largest_classes"):
                lines.append("\n### Largest Classes (by method count)")
                lines.append("| Class | File | Methods | Bases | Decorators |")
                lines.append("|-------|------|---------|-------|------------|")
                for cls in ast_data["largest_classes"][:10]:
                    bases_str = ", ".join(cls.get("bases", [])[:3]) or "—"
                    decos = ", ".join(cls.get("decorators", [])[:3]) or "—"
                    lines.append(
                        f"| {cls['name']} | `{cls['file']}` | "
                        f"{cls['method_count']} | {bases_str} | {decos} |"
                    )

            # Inheritance tree
            if ast_data.get("inheritance_tree"):
                lines.append("\n### Inheritance Tree")
                for base, children in sorted(
                    ast_data["inheritance_tree"].items(),
                    key=lambda x: -len(x[1])
                )[:15]:
                    lines.append(f"- **{base}** ← {', '.join(children[:8])}")

            # Top imports (import graph)
            if ast_data.get("top_imports"):
                lines.append("\n### Most-Imported Modules (Import Graph)")
                lines.append("| Module | Importing Files |")
                lines.append("|--------|----------------|")
                for mod, count in ast_data["top_imports"][:15]:
                    lines.append(f"| `{mod}` | {count} files |")

        # Framework Fingerprints
        drg = a.get("deep_ripgrep", {})
        if drg.get("detected_frameworks"):
            lines.append("\n## Detected Frameworks & Libraries")
            lines.append("| Framework | Occurrences |")
            lines.append("|-----------|-------------|")
            for fw, count in sorted(drg["detected_frameworks"].items(), key=lambda x: -x[1]):
                lines.append(f"| {fw} | {count} |")

        # Hotspots
        if drg.get("hotspots"):
            lines.append("\n## Architecture Hotspots")
            lines.append("Files matching the most architectural patterns (complexity hubs):")
            lines.append("| File | Pattern Hits |")
            lines.append("|------|-------------|")
            for h in drg["hotspots"][:10]:
                lines.append(f"| `{h['file']}` | {h['pattern_hits']} |")

        # Complexity Hotspots
        hs = a.get("hotspots", {})
        if hs:
            lines.append("\n## Complexity Analysis")
            lines.append(f"- **Files > 500 LOC**: {hs.get('files_over_500_loc', 0)}")
            lines.append(f"- **Files > 1000 LOC**: {hs.get('files_over_1000_loc', 0)}")
            lines.append(f"- **Median File LOC**: {hs.get('median_file_loc', 0)}")
            if hs.get("largest_files"):
                lines.append("\n### Largest Source Files")
                lines.append("| File | LOC |")
                lines.append("|------|-----|")
                for lf in hs["largest_files"][:10]:
                    lines.append(f"| `{lf['file']}` | {lf['loc']:,} |")

        # Design Patterns
        dp = a.get("design_patterns", {})
        if dp:
            lines.append("\n## Design Patterns Detected")
            for pattern_name, pattern_files in dp.items():
                lines.append(f"\n### {pattern_name} ({len(pattern_files)} files)")
                for pf in pattern_files[:5]:
                    lines.append(f"- `{pf}`")

        # Call Graph
        cg = a.get("call_graph", {})
        if cg:
            lines.append("\n## Call Graph Analysis")
            lines.append(f"- **Unique Call Targets**: {cg.get('total_unique_calls', 0)}")
            if cg.get("most_called_functions"):
                lines.append("\n### Most-Called Functions")
                lines.append("| Function | Calls | Defined In |")
                lines.append("|----------|-------|------------|")
                for fn in cg["most_called_functions"][:15]:
                    lines.append(f"| `{fn['name']}` | {fn['call_count']} | `{fn['defined_in']}` |")

        # API Surface
        api = a.get("api_surface", {})
        if api:
            lines.append("\n## API Surface")
            lines.append(f"- **Total API Surface**: {api.get('total_api_surface', 0)} endpoints/tools")
            if api.get("http_route_files"):
                lines.append(f"- **HTTP Route Files**: {len(api['http_route_files'])}")
                for rf in api["http_route_files"][:8]:
                    lines.append(f"  - `{rf}`")
            if api.get("cli_files"):
                lines.append(f"- **CLI Command Files**: {len(api['cli_files'])}")
                for cf in api["cli_files"][:5]:
                    lines.append(f"  - `{cf}`")
            if api.get("tool_registrations"):
                lines.append(f"- **Tool Registrations**: {len(api['tool_registrations'])}")
                for tf in api["tool_registrations"][:8]:
                    lines.append(f"  - `{tf}`")

        # Architecture Style Classification
        arch_style = a.get("architecture_style", {})
        if arch_style:
            lines.append("\n## Architecture Style Classification")
            lines.append(f"- **Primary Style**: {arch_style.get('primary_style', 'unknown')}")
            lines.append(f"- **Confidence Score**: {arch_style.get('confidence_score', 0)}")
            if arch_style.get("all_indicators"):
                lines.append("\n### Style Indicators")
                for style, score in sorted(arch_style["all_indicators"].items(), key=lambda x: -x[1]):
                    lines.append(f"- {style}: {score}")

        # Entry Points
        lines.append("\n## Entry Points")
        for ep in a["entry_points"]:
            lines.append(f"- `{ep}`")

        # Architecture (ripgrep counts)
        lines.append("\n## Architecture Patterns (Ripgrep)")
        for pat, count in sorted(a["architecture"].items(), key=lambda x: -x[1]):
            lines.append(f"- **{pat}**: {count} occurrences")

        # Dependencies
        lines.append("\n## Dependencies")
        for ecosystem, deps in a["dependencies"].items():
            lines.append(f"### {ecosystem}")
            if isinstance(deps, list):
                for d in deps[:30]:
                    lines.append(f"- {d}")

        # Debt
        lines.append("\n## Technical Debt Markers")
        total_debt = sum(a["debt"].values())
        lines.append(f"- **Total Markers**: {total_debt}")
        for marker, count in a["debt"].items():
            if count > 0:
                lines.append(f"  - {marker}: {count}")

        # Gitingest summary
        gi = a.get("gitingest", {})
        if gi:
            lines.append("\n## Gitingest Dump")
            lines.append(f"- **Total Characters**: {gi.get('total_chars', 0):,}")
            lines.append(f"- **Estimated Words**: {gi.get('estimated_words', 0):,}")
            lines.append(f"- **Files Included**: {gi.get('files_included', 0)}")
            lines.append(f"- **Files Skipped (cap)**: {gi.get('files_skipped_cap', 0)}")
            if gi.get("dump_path"):
                lines.append(f"- **Dump Path**: `{gi['dump_path']}`")

        # Config
        lines.append("\n## Configuration Files")
        for cf in a["config_files"]:
            lines.append(f"- `{cf}`")

        return "\n".join(lines)

    def _format_comparison_report(self, source: Dict, target: Dict, comparison: Dict) -> str:
        """Format the pair-wise comparison as a comprehensive markdown report."""
        s_name = source["name"]
        t_name = target["name"]
        lines = [f"# Comprehensive Comparison: {s_name} vs {t_name}\n"]

        # Executive Summary
        lines.append("## Executive Summary")
        s_loc = source["metrics"]["total_loc"]
        t_loc = target["metrics"]["total_loc"]
        s_files = source["metrics"]["source_files"]
        t_files = target["metrics"]["source_files"]
        lines.append(
            f"This report compares **{s_name}** ({s_loc:,} LOC, {s_files} source files) "
            f"with **{t_name}** ({t_loc:,} LOC, {t_files} source files) "
            f"for the purpose of porting and assimilation analysis.\n"
        )

        # At a Glance
        lines.append("## At a Glance")
        lines.append(f"| Dimension | {s_name} | {t_name} |")
        lines.append("|-----------|----------|----------|")
        lines.append(f"| Total Files | {source['structure']['total_files']} | {target['structure']['total_files']} |")
        lines.append(f"| Source Files | {s_files} | {t_files} |")
        lines.append(f"| Total LOC | {s_loc:,} | {t_loc:,} |")
        lines.append(f"| Entry Points | {len(source['entry_points'])} | {len(target['entry_points'])} |")
        lines.append(f"| Config Files | {len(source['config_files'])} | {len(target['config_files'])} |")

        # LOC by lang
        all_langs = set(source["metrics"]["lines_of_code"].keys()) | set(target["metrics"]["lines_of_code"].keys())
        for lang in sorted(all_langs):
            sl = source["metrics"]["lines_of_code"].get(lang, 0)
            tl = target["metrics"]["lines_of_code"].get(lang, 0)
            lines.append(f"| LOC ({lang}) | {sl:,} | {tl:,} |")

        # Debt
        s_debt = sum(source["debt"].values())
        t_debt = sum(target["debt"].values())
        lines.append(f"| Tech Debt Markers | {s_debt} | {t_debt} |")
        lines.append("")

        # Architecture Comparison
        lines.append("## Architecture Comparison")
        lines.append(f"| Pattern | {s_name} | {t_name} | Delta |")
        lines.append("|---------|----------|----------|-------|")
        for pat, data in sorted(comparison["architecture_delta"].items(), key=lambda x: -abs(x[1]["delta"])):
            lines.append(f"| {pat} | {data['source']} | {data['target']} | {data['delta']:+d} |")
        lines.append("")

        # AST Comparison
        s_ast = source.get("ast", {})
        t_ast = target.get("ast", {})
        if s_ast or t_ast:
            lines.append("## AST Analysis Comparison")
            lines.append(f"| Dimension | {s_name} | {t_name} |")
            lines.append("|-----------|----------|----------|")
            lines.append(f"| Python Files | {s_ast.get('total_python_files', 0)} | {t_ast.get('total_python_files', 0)} |")
            lines.append(f"| Classes | {s_ast.get('total_classes', 0)} | {t_ast.get('total_classes', 0)} |")
            lines.append(f"| Functions | {s_ast.get('total_functions', 0)} | {t_ast.get('total_functions', 0)} |")
            lines.append(f"| Async Functions | {s_ast.get('async_function_count', 0)} | {t_ast.get('async_function_count', 0)} |")
            lines.append("")

            # Inheritance tree side by side (top bases)
            s_tree = s_ast.get("inheritance_tree", {})
            t_tree = t_ast.get("inheritance_tree", {})
            if s_tree or t_tree:
                lines.append("### Key Inheritance Hierarchies")
                all_bases = sorted(set(list(s_tree.keys())[:5] + list(t_tree.keys())[:5]))
                for base in all_bases:
                    s_children = ", ".join(s_tree.get(base, ["—"])[:5])
                    t_children = ", ".join(t_tree.get(base, ["—"])[:5])
                    lines.append(f"- **{base}** → {s_name}: [{s_children}] | {t_name}: [{t_children}]")
                lines.append("")

        # Framework Comparison
        s_fw = source.get("deep_ripgrep", {}).get("detected_frameworks", {})
        t_fw = target.get("deep_ripgrep", {}).get("detected_frameworks", {})
        if s_fw or t_fw:
            lines.append("## Framework & Library Fingerprints")
            all_fws = sorted(set(list(s_fw.keys()) + list(t_fw.keys())))
            lines.append(f"| Framework | {s_name} | {t_name} |")
            lines.append("|-----------|----------|----------|")
            for fw in all_fws:
                lines.append(f"| {fw} | {s_fw.get(fw, '—')} | {t_fw.get(fw, '—')} |")
            lines.append("")

        # Complexity Comparison
        s_hs = source.get("hotspots", {})
        t_hs = target.get("hotspots", {})
        if s_hs or t_hs:
            lines.append("## Complexity Comparison")
            lines.append(f"| Dimension | {s_name} | {t_name} |")
            lines.append("|-----------|----------|----------|")
            lines.append(f"| Files > 500 LOC | {s_hs.get('files_over_500_loc', 0)} | {t_hs.get('files_over_500_loc', 0)} |")
            lines.append(f"| Files > 1000 LOC | {s_hs.get('files_over_1000_loc', 0)} | {t_hs.get('files_over_1000_loc', 0)} |")
            lines.append(f"| Median File LOC | {s_hs.get('median_file_loc', 0)} | {t_hs.get('median_file_loc', 0)} |")
            lines.append("")

        # Gitingest Summary
        s_gi = source.get("gitingest", {})
        t_gi = target.get("gitingest", {})
        if s_gi or t_gi:
            lines.append("## Gitingest Dump Summary")
            lines.append(f"| Dimension | {s_name} | {t_name} |")
            lines.append("|-----------|----------|----------|")
            lines.append(f"| Total Characters | {s_gi.get('total_chars', 0):,} | {t_gi.get('total_chars', 0):,} |")
            lines.append(f"| Estimated Words | {s_gi.get('estimated_words', 0):,} | {t_gi.get('estimated_words', 0):,} |")
            lines.append(f"| Files Included | {s_gi.get('files_included', 0)} | {t_gi.get('files_included', 0)} |")
            if s_gi.get("dump_path") or t_gi.get("dump_path"):
                lines.append(f"| Dump Path | `{s_gi.get('dump_path', '—')}` | `{t_gi.get('dump_path', '—')}` |")
            lines.append("")

        # Design Pattern Comparison
        s_dp = source.get("design_patterns", {})
        t_dp = target.get("design_patterns", {})
        if s_dp or t_dp:
            lines.append("## Design Patterns Comparison")
            all_patterns = sorted(set(list(s_dp.keys()) + list(t_dp.keys())))
            lines.append(f"| Pattern | {s_name} | {t_name} |")
            lines.append("|---------|----------|----------|")
            for p in all_patterns:
                s_count = len(s_dp.get(p, []))
                t_count = len(t_dp.get(p, []))
                lines.append(f"| {p} | {s_count} files | {t_count} files |")
            lines.append("")

        # Call Graph Comparison
        s_cg = source.get("call_graph", {})
        t_cg = target.get("call_graph", {})
        if s_cg or t_cg:
            lines.append("## Call Graph Comparison")
            lines.append(f"| Dimension | {s_name} | {t_name} |")
            lines.append("|-----------|----------|----------|")
            lines.append(f"| Unique Call Targets | {s_cg.get('total_unique_calls', 0)} | {t_cg.get('total_unique_calls', 0)} |")
            lines.append("")

        # API Surface Comparison
        s_api = source.get("api_surface", {})
        t_api = target.get("api_surface", {})
        if s_api or t_api:
            lines.append("## API Surface Comparison")
            lines.append(f"| Dimension | {s_name} | {t_name} |")
            lines.append("|-----------|----------|----------|")
            lines.append(f"| Total API Surface | {s_api.get('total_api_surface', 0)} | {t_api.get('total_api_surface', 0)} |")
            lines.append(f"| HTTP Route Files | {len(s_api.get('http_route_files', []))} | {len(t_api.get('http_route_files', []))} |")
            lines.append(f"| CLI Files | {len(s_api.get('cli_files', []))} | {len(t_api.get('cli_files', []))} |")
            lines.append(f"| Tool Registrations | {len(s_api.get('tool_registrations', []))} | {len(t_api.get('tool_registrations', []))} |")
            lines.append("")

        # Architecture Style Comparison
        s_arch = source.get("architecture_style", {})
        t_arch = target.get("architecture_style", {})
        if s_arch or t_arch:
            lines.append("## Architecture Style Comparison")
            lines.append(f"| Dimension | {s_name} | {t_name} |")
            lines.append("|-----------|----------|----------|")
            lines.append(f"| Primary Style | {s_arch.get('primary_style', '—')} | {t_arch.get('primary_style', '—')} |")
            lines.append(f"| Confidence | {s_arch.get('confidence_score', 0)} | {t_arch.get('confidence_score', 0)} |")
            lines.append("")

        # Directory Structure
        lines.append("## Directory Structure Comparison")
        lines.append(f"\n### Common Directories ({len(comparison['common_dirs'])})")
        for d in comparison["common_dirs"][:20]:
            lines.append(f"- `{d}`")
        lines.append(f"\n### Only in {s_name} ({len(comparison['source_only_dirs'])})")
        for d in comparison["source_only_dirs"][:20]:
            lines.append(f"- `{d}`")
        lines.append(f"\n### Only in {t_name} ({len(comparison['target_only_dirs'])})")
        for d in comparison["target_only_dirs"][:20]:
            lines.append(f"- `{d}`")
        lines.append("")

        # Dependency Comparison
        lines.append("## Dependency Comparison")
        lines.append(f"\n### Shared Dependencies ({len(comparison['shared_deps'])})")
        for d in comparison["shared_deps"][:30]:
            lines.append(f"- {d}")
        lines.append(f"\n### Only in {s_name} ({len(comparison['source_only_deps'])})")
        for d in comparison["source_only_deps"][:30]:
            lines.append(f"- {d}")
        lines.append(f"\n### Only in {t_name} ({len(comparison['target_only_deps'])})")
        for d in comparison["target_only_deps"][:30]:
            lines.append(f"- {d}")
        lines.append("")

        # Technical Debt
        lines.append("## Technical Debt Comparison")
        lines.append(f"| Marker | {s_name} | {t_name} |")
        lines.append("|--------|----------|----------|")
        all_markers = set(source["debt"].keys()) | set(target["debt"].keys())
        for m in sorted(all_markers):
            lines.append(f"| {m} | {source['debt'].get(m, 0)} | {target['debt'].get(m, 0)} |")
        lines.append("")

        # Strategic Assessment (enhanced)
        lines.append("## Strategic Assessment for Porting / Assimilation")
        lines.append(
            f"Based on this forensic analysis, **{s_name}** and **{t_name}** share "
            f"{len(comparison['shared_deps'])} dependencies. "
        )
        if s_loc > t_loc:
            lines.append(f"**{s_name}** is the larger codebase by {s_loc - t_loc:,} lines.")
        else:
            lines.append(f"**{t_name}** is the larger codebase by {t_loc - s_loc:,} lines.")

        # Migration complexity scoring
        shared_fw = set(s_fw.keys()) & set(t_fw.keys())
        unique_s_fw = set(s_fw.keys()) - set(t_fw.keys())
        unique_t_fw = set(t_fw.keys()) - set(s_fw.keys())
        lines.append(f"\n### Framework Overlap")
        lines.append(f"- **Shared**: {', '.join(shared_fw) if shared_fw else 'None'}")
        lines.append(f"- **Only {s_name}**: {', '.join(unique_s_fw) if unique_s_fw else 'None'}")
        lines.append(f"- **Only {t_name}**: {', '.join(unique_t_fw) if unique_t_fw else 'None'}")
        lines.append("")

        # Entry Points side-by-side
        lines.append("## Entry Points")
        lines.append(f"\n### {s_name}")
        for ep in source["entry_points"]:
            lines.append(f"- `{ep}`")
        lines.append(f"\n### {t_name}")
        for ep in target["entry_points"]:
            lines.append(f"- `{ep}`")

        return "\n".join(lines)


# ── CLI Entry Point ──
# Allows agents to call this script directly via code_execution_tool
# when it's not available as a registered tool in their toolset.

if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Borg Codebase Forensic Analysis Tool"
    )
    parser.add_argument(
        "--action", choices=["analyze", "compare"], default="compare",
        help="Action: 'analyze' single codebase or 'compare' two codebases"
    )
    parser.add_argument(
        "--source_path", "--source", required=False,
        help="Path to source codebase (required for compare)"
    )
    parser.add_argument(
        "--target_path", "--target", required=True,
        help="Path to target codebase"
    )
    parser.add_argument(
        "--output_dir", "--output", default="/tmp/borg-output",
        help="Directory to save reports and gitingest dumps"
    )
    parser.add_argument(
        "--report_depth", choices=["comprehensive", "summary"], default="comprehensive",
        help="Report depth: 'comprehensive' (multi-file, 5K+ lines) or 'summary' (single file)"
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Create a minimal mock agent for standalone execution
    class _MockAgent:
        class _MockConfig:
            def __init__(self):
                self.chat_model = type("M", (), {"name": "cli"})()
        config = _MockConfig()

    borg = BorgCompare(agent=_MockAgent(), name="borg_compare", method=None, args={}, message="", loop_data=None)
    borg.output_dir = args.output_dir
    borg.report_depth = args.report_depth

    if args.action == "analyze":
        print(f"🔬 Analyzing: {args.target_path}")
        analysis = borg._deep_analysis(args.target_path)

        if args.report_depth == "comprehensive":
            manifest = borg._write_comprehensive_report(analysis)
            print(f"✅ Comprehensive report written to: {args.output_dir}")
            print(manifest)
        else:
            report = borg._format_analysis_report(analysis)
            report_path = os.path.join(args.output_dir, "analysis_report.md")
            with open(report_path, "w") as f:
                f.write(report)
            print(f"✅ Summary report saved: {report_path}")
            print(report[:2000])

        print(f"📊 LOC: {analysis['metrics']['total_loc']:,}")
        print(f"📁 Files: {analysis['metrics']['source_files']}")
        print(f"🏛️ Architecture: {analysis.get('architecture_style', {}).get('primary_style', 'unknown')}")

    elif args.action == "compare":
        if not args.source_path:
            parser.error("--source_path is required for compare action")
        print(f"🔬 Analyzing source: {args.source_path}")
        source = borg._deep_analysis(args.source_path)
        print(f"🔬 Analyzing target: {args.target_path}")
        target = borg._deep_analysis(args.target_path)
        print(f"📊 Comparing...")
        comparison = borg._compare_pair(source, target)

        if args.report_depth == "comprehensive":
            manifest_src = borg._write_comprehensive_report(source, prefix="source_")
            manifest_tgt = borg._write_comprehensive_report(target, prefix="target_")
            comp_report = borg._format_comparison_report(source, target, comparison)
            comp_path = os.path.join(args.output_dir, "08_strategic_comparison.md")
            with open(comp_path, "w") as f:
                f.write(comp_report)
            index = borg._write_comparison_index(source, target, manifest_src, manifest_tgt, comp_path)
            print(f"✅ Comprehensive comparison written to: {args.output_dir}")
            print(index)
        else:
            report = borg._format_comparison_report(source, target, comparison)
            report_path = os.path.join(args.output_dir, "comparison_report.md")
            with open(report_path, "w") as f:
                f.write(report)
            print(f"✅ Comparison report saved: {report_path}")
            print(report[:2000])

        print(f"📊 Source LOC: {source['metrics']['total_loc']:,}")
        print(f"📊 Target LOC: {target['metrics']['total_loc']:,}")

