"""Borg Forensic Analysis Methods — Gold-standard codebase forensic dimensions.

Extracted from borg_compare.py for modularity. These methods implement 6 advanced
forensic analysis dimensions that complement the original 15 in BorgCompare.

Usage: Instantiate BorgForensics and call methods directly with a root path.
"""
from __future__ import annotations

import ast
import os
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Set

from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS

# File extensions eligible for forensic analysis
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".rb", ".php", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift",
    ".kt", ".scala", ".sh", ".bash", ".vue", ".svelte",
}

# DUP-3: Uses shared DEFAULT_PROJECT_SKIP_DIRS + forensic-specific extras.
EXCLUDE_DIRS = set(DEFAULT_PROJECT_SKIP_DIRS) | {
    ".tox", ".mypy_cache", ".pytest_cache",
}


class BorgForensics:
    """Gold-standard forensic analysis methods for codebase analysis."""

    def __init__(self):
        self.source_extensions = SOURCE_EXTENSIONS
        self.exclude_dirs = EXCLUDE_DIRS

    def _iter_source_files(self, root: str):
        """Yield (rel_path, abs_path) for source files under root."""
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.exclude_dirs]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in self.source_extensions:
                    abs_path = os.path.join(dirpath, fname)
                    rel_path = os.path.relpath(abs_path, root)
                    yield rel_path, abs_path

    def _read_lines(self, path: str) -> List[str]:
        """Read file lines safely, returning [] on decode errors."""
        try:
            with open(path, "r", errors="replace") as f:
                return f.readlines()
        except Exception:
            return []

    # ── Dimension 14: Cyclomatic Complexity ──

    def cyclomatic_complexity(self, root: str) -> Dict[str, Any]:
        """AST-based cyclomatic complexity analysis for Python files.

        Counts decision points (if/elif/for/while/except/with/and/or/assert/
        comprehension) per function and aggregates at file level.
        """
        all_functions: List[Dict] = []
        file_complexity: Dict[str, Dict] = {}
        decision_nodes = (
            ast.If, ast.For, ast.While, ast.ExceptHandler,
            ast.With, ast.Assert,
        )

        for rel_path, abs_path in self._iter_source_files(root):
            if not abs_path.endswith(".py"):
                continue
            try:
                with open(abs_path, "r", errors="replace") as f:
                    tree = ast.parse(f.read(), filename=abs_path)
            except (SyntaxError, ValueError):
                continue

            funcs_in_file = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cc = 1  # base complexity
                    for child in ast.walk(node):
                        if isinstance(child, decision_nodes):
                            cc += 1
                        elif isinstance(child, ast.BoolOp):
                            cc += len(child.values) - 1
                        elif isinstance(child, (ast.ListComp, ast.SetComp,
                                                 ast.GeneratorExp, ast.DictComp)):
                            cc += 1
                    fn_info = {
                        "name": node.name,
                        "file": rel_path,
                        "line": node.lineno,
                        "complexity": cc,
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                    }
                    all_functions.append(fn_info)
                    funcs_in_file.append(fn_info)

            if funcs_in_file:
                ccs = [f["complexity"] for f in funcs_in_file]
                file_complexity[rel_path] = {
                    "max_cc": max(ccs),
                    "avg_cc": round(sum(ccs) / len(ccs), 2),
                    "func_count": len(ccs),
                    "total_cc": sum(ccs),
                }

        complexities = [f["complexity"] for f in all_functions]
        sorted_complexities = sorted(complexities) if complexities else [0]
        median = sorted_complexities[len(sorted_complexities) // 2] if sorted_complexities else 0

        distribution = {
            "1-5 (simple)": sum(1 for c in complexities if 1 <= c <= 5),
            "6-10 (moderate)": sum(1 for c in complexities if 6 <= c <= 10),
            "11-20 (complex)": sum(1 for c in complexities if 11 <= c <= 20),
            "21-50 (very complex)": sum(1 for c in complexities if 21 <= c <= 50),
            "50+ (untestable)": sum(1 for c in complexities if c > 50),
        }

        sorted_file_complexity = sorted(
            file_complexity.items(), key=lambda x: -x[1]["max_cc"]
        )

        return {
            "total_functions_analyzed": len(all_functions),
            "avg_complexity": round(sum(complexities) / len(complexities), 2) if complexities else 0,
            "median_complexity": median,
            "max_complexity": max(complexities) if complexities else 0,
            "distribution": distribution,
            "top_complex_functions": sorted(all_functions, key=lambda x: -x["complexity"])[:100],
            "file_complexity": sorted_file_complexity[:50],
        }

    # ── Dimension 15: Comment Density ──

    def comment_density(self, root: str) -> Dict[str, Any]:
        """Analyze comment-to-code ratio across all source files."""
        COMMENT_PATTERNS = {
            ".py": (r"^\s*#", None),
            ".js": (r"^\s*//", (r"/\*", r"\*/")),
            ".ts": (r"^\s*//", (r"/\*", r"\*/")),
            ".jsx": (r"^\s*//", (r"/\*", r"\*/")),
            ".tsx": (r"^\s*//", (r"/\*", r"\*/")),
            ".java": (r"^\s*//", (r"/\*", r"\*/")),
            ".go": (r"^\s*//", (r"/\*", r"\*/")),
            ".rs": (r"^\s*//", (r"/\*", r"\*/")),
            ".rb": (r"^\s*#", (r"=begin", r"=end")),
            ".php": (r"^\s*(//|#)", (r"/\*", r"\*/")),
            ".c": (r"^\s*//", (r"/\*", r"\*/")),
            ".cpp": (r"^\s*//", (r"/\*", r"\*/")),
            ".h": (r"^\s*//", (r"/\*", r"\*/")),
            ".hpp": (r"^\s*//", (r"/\*", r"\*/")),
            ".sh": (r"^\s*#", None),
            ".bash": (r"^\s*#", None),
        }

        file_results: List[Dict] = []
        total_code = total_comments = total_blank = 0

        for rel_path, abs_path in self._iter_source_files(root):
            ext = os.path.splitext(rel_path)[1].lower()
            line_pattern, _ = COMMENT_PATTERNS.get(ext, (r"^\s*#", None))
            lines = self._read_lines(abs_path)
            code_lines = comment_lines = blank_lines = 0

            for line in lines:
                stripped = line.strip()
                if not stripped:
                    blank_lines += 1
                elif re.match(line_pattern, line):
                    comment_lines += 1
                else:
                    code_lines += 1

            total_code += code_lines
            total_comments += comment_lines
            total_blank += blank_lines

            density_pct = round(comment_lines / (code_lines + comment_lines) * 100, 1) if (code_lines + comment_lines) > 0 else 0
            file_results.append({
                "file": rel_path,
                "code_lines": code_lines,
                "comment_lines": comment_lines,
                "blank_lines": blank_lines,
                "comment_density_pct": density_pct,
            })

        overall_density = round(total_comments / (total_code + total_comments) * 100, 1) if (total_code + total_comments) > 0 else 0
        zero_comment_files = [f for f in file_results if f["comment_lines"] == 0 and f["code_lines"] > 20]

        return {
            "overall_density_pct": overall_density,
            "total_code_lines": total_code,
            "total_comment_lines": total_comments,
            "total_blank_lines": total_blank,
            "total_files_analyzed": len(file_results),
            "files_with_most_comments": sorted(file_results, key=lambda x: -x["comment_density_pct"])[:30],
            "zero_comment_files": sorted(zero_comment_files, key=lambda x: -x["code_lines"])[:30],
        }

    # ── Dimension 16: Duplication Detection ──

    def duplication_detection(self, root: str, block_size: int = 5) -> Dict[str, Any]:
        """Hash-based repeated code block detection.

        Slides a window of `block_size` lines across each file, hashes the normalized
        content, and reports duplicate blocks across files.
        """
        block_hashes: Dict[str, List[Dict]] = defaultdict(list)

        for rel_path, abs_path in self._iter_source_files(root):
            lines = self._read_lines(abs_path)
            normalized = [re.sub(r"\s+", " ", l.strip()) for l in lines]

            for i in range(len(normalized) - block_size + 1):
                block = "\n".join(normalized[i:i + block_size])
                if len(block.strip()) < 20:
                    continue
                from python.helpers.hashing import content_hash_short
                h = content_hash_short(block, length=12)
                block_hashes[h].append({
                    "file": rel_path,
                    "start_line": i + 1,
                    "preview": normalized[i][:80],
                })

        duplicates = {h: locs for h, locs in block_hashes.items() if len(locs) > 1}
        cross_file_dups = {}
        for h, locs in duplicates.items():
            unique_files = set(loc["file"] for loc in locs)
            if len(unique_files) > 1:
                cross_file_dups[h] = locs

        top_dups = sorted(
            [{"hash": h, "occurrences": len(locs), "unique_files": len(set(l["file"] for l in locs)),
              "preview": locs[0]["preview"], "locations": locs[:6]}
             for h, locs in duplicates.items()],
            key=lambda x: -x["occurrences"]
        )[:40]

        top_cross = sorted(
            [{"hash": h, "occurrences": len(locs), "unique_files": len(set(l["file"] for l in locs)),
              "preview": locs[0]["preview"], "locations": locs[:6]}
             for h, locs in cross_file_dups.items()],
            key=lambda x: -x["occurrences"]
        )[:20]

        return {
            "block_size": block_size,
            "total_duplicate_blocks": sum(len(locs) for locs in duplicates.values()),
            "unique_duplicate_patterns": len(duplicates),
            "cross_file_duplicates": len(cross_file_dups),
            "top_duplicates": top_dups,
            "top_cross_file_duplicates": top_cross,
        }

    # ── Dimension 17: Security Markers ──

    def security_markers(self, root: str) -> Dict[str, Any]:
        """SAST-style security scan with CVSS-inspired risk scoring (0-100)."""
        PATTERNS = {
            "eval_usage": {"pattern": r"\beval\s*\(", "severity": "critical", "weight": 15},
            "exec_usage": {"pattern": r"\bexec\s*\(", "severity": "critical", "weight": 15},
            "hardcoded_password": {"pattern": r"(password|passwd|pwd)\s*=\s*['\"][^'\"]+['\"]", "severity": "critical", "weight": 20},
            "hardcoded_secret": {"pattern": r"(secret|api_key|apikey|token)\s*=\s*['\"][^'\"]+['\"]", "severity": "critical", "weight": 20},
            "subprocess_shell": {"pattern": r"subprocess\.(call|run|Popen)\s*\([^)]*shell\s*=\s*True", "severity": "high", "weight": 10},
            "sql_injection_risk": {"pattern": r"(execute|cursor\.execute)\s*\(\s*['\"].*%s|f['\"].*\{", "severity": "critical", "weight": 15},
            "pickle_load": {"pattern": r"pickle\.load[s]?\s*\(", "severity": "high", "weight": 10},
            "yaml_unsafe": {"pattern": r"yaml\.load\s*\([^)]*Loader\s*=\s*yaml\.(?:Unsafe|Full)Loader", "severity": "high", "weight": 8},
            "debug_enabled": {"pattern": r"DEBUG\s*=\s*True", "severity": "medium", "weight": 5},
            "cors_wildcard": {"pattern": r"Access-Control-Allow-Origin.*\*|cors.*origin.*\*", "severity": "medium", "weight": 5},
            "hardcoded_credentials": {"pattern": r"(username|user)\s*=\s*['\"][^'\"]+['\"]", "severity": "high", "weight": 8},
            "temp_file_risk": {"pattern": r"(mktemp|tmpfile|tempfile\.mk)", "severity": "low", "weight": 2},
        }

        findings: Dict[str, List[str]] = defaultdict(list)
        severity_counts = Counter()
        total_weight = 0

        for rel_path, abs_path in self._iter_source_files(root):
            try:
                with open(abs_path, "r", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            for name, info in PATTERNS.items():
                if re.search(info["pattern"], content, re.IGNORECASE):
                    findings[name].append(rel_path)
                    severity_counts[info["severity"]] += 1
                    total_weight += info["weight"]

        risk_score = min(100, total_weight)

        return {
            "risk_score": risk_score,
            "total_findings": sum(len(files) for files in findings.values()),
            "patterns_checked": len(PATTERNS),
            "severity_counts": dict(severity_counts),
            "findings": dict(findings),
        }

    # ── Dimension 18: Test Analysis ──

    def test_analysis(self, root: str) -> Dict[str, Any]:
        """Detect test files, frameworks, and compute test-to-code ratios."""
        TEST_PATTERNS = [
            r"test_.*\.py$", r".*_test\.py$", r".*\.test\.[jt]sx?$",
            r".*\.spec\.[jt]sx?$", r"__tests__/.*\.[jt]sx?$",
        ]
        FRAMEWORK_PATTERNS = {
            "pytest": r"import pytest|from pytest|@pytest",
            "unittest": r"import unittest|from unittest|class.*\(.*TestCase\)",
            "jest": r"describe\s*\(|it\s*\(|expect\s*\(",
            "mocha": r"describe\s*\(|it\s*\(|chai",
        }

        test_files: List[Dict] = []
        source_files: List[Dict] = []
        frameworks_detected: Set[str] = set()
        test_func_count = 0
        assertion_count = 0

        for rel_path, abs_path in self._iter_source_files(root):
            lines = self._read_lines(abs_path)
            loc = len([l for l in lines if l.strip()])
            is_test = any(re.search(p, rel_path) for p in TEST_PATTERNS)

            if is_test:
                test_files.append({"file": rel_path, "loc": loc})
                content = "".join(lines)
                for fw, pat in FRAMEWORK_PATTERNS.items():
                    if re.search(pat, content):
                        frameworks_detected.add(fw)
                test_func_count += sum(1 for l in lines if re.match(r"\s*(def test_|async def test_|it\(|test\()", l))
                assertion_count += sum(1 for l in lines if re.search(r"(assert |self\.assert|expect\(|\.to(Equal|Be|Have))", l))
            else:
                source_files.append({"file": rel_path, "loc": loc})

        test_loc = sum(f["loc"] for f in test_files)
        source_loc = sum(f["loc"] for f in source_files)
        ratio = round(test_loc / source_loc * 100, 1) if source_loc > 0 else 0

        return {
            "test_file_count": len(test_files),
            "source_file_count": len(source_files),
            "test_loc": test_loc,
            "source_loc": source_loc,
            "test_to_code_ratio_pct": ratio,
            "test_function_count": test_func_count,
            "assertion_count": assertion_count,
            "test_frameworks_detected": sorted(frameworks_detected),
            "test_files": sorted(test_files, key=lambda x: -x["loc"])[:50],
        }

    # ── Dimension 19: Module Coupling ──

    def module_coupling(self, root: str) -> Dict[str, Any]:
        """Afferent/efferent coupling and instability index per module.

        Uses Robert C. Martin's instability formula: I = Ce / (Ca + Ce)
        """
        module_imports: Dict[str, Set[str]] = defaultdict(set)

        for rel_path, abs_path in self._iter_source_files(root):
            if not abs_path.endswith(".py"):
                continue
            module_name = rel_path.replace(os.sep, ".").replace(".py", "")
            try:
                with open(abs_path, "r", errors="replace") as f:
                    tree = ast.parse(f.read(), filename=abs_path)
            except (SyntaxError, ValueError):
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module_imports[module_name].add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        module_imports[module_name].add(node.module.split(".")[0])

        all_modules = set(module_imports.keys())
        afferent: Dict[str, Set[str]] = defaultdict(set)
        efferent: Dict[str, Set[str]] = defaultdict(set)

        for mod, imports in module_imports.items():
            internal_imports = imports & {m.split(".")[0] for m in all_modules}
            efferent[mod] = internal_imports
            for imp in internal_imports:
                afferent[imp].add(mod)

        coupling_table: List[Dict] = []
        instabilities: List[float] = []
        for mod in all_modules:
            ca = len(afferent.get(mod, set()))
            ce = len(efferent.get(mod, set()))
            instability = round(ce / (ca + ce), 3) if (ca + ce) > 0 else 0.5
            instabilities.append(instability)
            coupling_table.append({
                "module": mod,
                "afferent_coupling": ca,
                "efferent_coupling": ce,
                "instability": instability,
                "afferent_sources": sorted(afferent.get(mod, set()))[:5],
                "efferent_targets": sorted(efferent.get(mod, set()))[:5],
            })

        coupling_table.sort(key=lambda x: -x["afferent_coupling"])

        return {
            "module_count": len(all_modules),
            "avg_instability": round(sum(instabilities) / len(instabilities), 3) if instabilities else 0,
            "coupling_table": coupling_table[:50],
            "most_depended_on": sorted(coupling_table, key=lambda x: -x["afferent_coupling"])[:10],
            "most_dependent": sorted(coupling_table, key=lambda x: -x["efferent_coupling"])[:10],
        }
