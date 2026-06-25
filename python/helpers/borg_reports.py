"""Borg Multi-File Report Writer — Comprehensive forensic report generation.

Extracted from borg_compare.py for modularity. Generates 8 sub-reports + index
from a BorgCompare analysis dict. Each sub-report is a standalone markdown file
covering a specific forensic dimension.

Usage:
    writer = BorgReportWriter("/path/to/output")
    manifest = writer.write_comprehensive_report(analysis_dict)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


class BorgReportWriter:
    """Multi-file forensic report generator for Borg analysis output."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir

    def write_comprehensive_report(self, analysis: Dict, prefix: str = "") -> str:
        """Write 8 sub-reports + index. Returns the manifest text."""
        os.makedirs(self.output_dir, exist_ok=True)
        a = analysis
        name = a["name"]
        reports_written: List[Tuple[str, int]] = []

        report_generators = [
            ("00_executive_summary.md", self.report_executive_summary),
            ("01_architecture_analysis.md", self.report_architecture),
            ("02_code_metrics_complexity.md", self.report_metrics_complexity),
            ("03_ast_deep_analysis.md", self.report_ast_deep),
            ("04_design_patterns.md", self.report_design_patterns),
            ("05_api_surface_dependencies.md", self.report_api_dependencies),
            ("06_code_quality_debt.md", self.report_quality_debt),
            ("07_call_graph_hotspots.md", self.report_call_graph_hotspots),
        ]

        for filename, generator in report_generators:
            content = generator(a)
            path = os.path.join(self.output_dir, f"{prefix}{filename}")
            self._write_file(path, content)
            reports_written.append((path, content.count("\n")))

        # Write index
        total_lines = sum(lc for _, lc in reports_written)
        index_lines = [f"# Borg Forensic Report Index: {name}\n"]
        index_lines.append(f"**Total report lines**: {total_lines:,}")
        index_lines.append(f"**Sub-reports**: {len(reports_written)}")
        index_lines.append(f"**Analysis dimensions**: 21\n")
        index_lines.append("## Report Files\n")
        index_lines.append("| # | Report | Lines | Path |")
        index_lines.append("|---|--------|-------|------|")
        for i, (rp, lc) in enumerate(reports_written):
            basename = os.path.basename(rp)
            index_lines.append(f"| {i} | {basename} | {lc:,} | `{rp}` |")
        index_lines.append("")
        index_content = "\n".join(index_lines)
        index_path = os.path.join(self.output_dir, f"{prefix}index.md")
        self._write_file(index_path, index_content)

        return index_content

    def write_comparison_index(self, source: Dict, target: Dict,
                               manifest_src: str, manifest_tgt: str,
                               comp_path: str) -> str:
        """Write master index for a comparison run."""
        lines = ["# Borg Forensic Comparison Index\n"]
        lines.append(f"**Source**: {source['name']}")
        lines.append(f"**Target**: {target['name']}\n")
        lines.append("## Source Reports\n")
        lines.append(manifest_src)
        lines.append("\n## Target Reports\n")
        lines.append(manifest_tgt)
        lines.append(f"\n## Comparison Report\n")
        lines.append(f"- `{comp_path}`")
        content = "\n".join(lines)
        index_path = os.path.join(self.output_dir, "comparison_index.md")
        self._write_file(index_path, content)
        return content

    def _write_file(self, path: str, content: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    # ── Sub-report generators ──

    def report_executive_summary(self, a: Dict) -> str:
        """00_executive_summary.md — overview + at-a-glance."""
        name = a["name"]
        L = [f"# Executive Summary: {name}\n"]

        L.append("## Overview\n")
        m = a["metrics"]
        s = a["structure"]
        L.append(f"**{name}** is a codebase with **{m['total_loc']:,}** lines of code across "
                 f"**{m['source_files']}** source files and **{s['total_files']}** total files.\n")

        arch = a.get("architecture_style", {})
        L.append(f"- **Architecture Style**: {arch.get('primary_style', 'unknown')} "
                 f"(confidence: {arch.get('confidence_score', 0)})")
        L.append(f"- **Entry Points**: {len(a['entry_points'])}")
        L.append(f"- **Config Files**: {len(a['config_files'])}")

        for key, label in [
            ("ast", [("total_classes", "Python Classes"), ("total_functions", "Python Functions"),
                     ("async_function_count", "Async Functions")]),
        ]:
            d = a.get(key, {})
            if d:
                for field, display in label:
                    L.append(f"- **{display}**: {d.get(field, 0)}")

        sec = a.get("security", {})
        if sec:
            L.append(f"- **Security Risk Score**: {sec.get('risk_score', 0)}/100")
            L.append(f"- **Security Findings**: {sec.get('total_findings', 0)}")

        ta = a.get("test_analysis", {})
        if ta:
            L.append(f"- **Test Files**: {ta.get('test_file_count', 0)}")
            L.append(f"- **Test-to-Code Ratio**: {ta.get('test_to_code_ratio_pct', 0)}%")

        cc = a.get("cyclomatic_complexity", {})
        if cc:
            L.append(f"- **Avg Cyclomatic Complexity**: {cc.get('avg_complexity', 0)}")
            L.append(f"- **Max Cyclomatic Complexity**: {cc.get('max_complexity', 0)}")

        cd = a.get("comment_density", {})
        if cd:
            L.append(f"- **Comment Density**: {cd.get('overall_density_pct', 0)}%")

        dup = a.get("duplication", {})
        if dup:
            L.append(f"- **Duplicate Code Blocks**: {dup.get('total_duplicate_blocks', 0)}")
            L.append(f"- **Cross-File Duplicates**: {dup.get('cross_file_duplicates', 0)}")

        mc = a.get("module_coupling", {})
        if mc:
            L.append(f"- **Modules Analyzed**: {mc.get('module_count', 0)}")
            L.append(f"- **Avg Instability**: {mc.get('avg_instability', 0)}")

        L.append("")

        # At a Glance table
        L.append("## At a Glance\n")
        L.append("| Dimension | Value |")
        L.append("|-----------|-------|")
        L.append(f"| Total Files | {s['total_files']} |")
        L.append(f"| Source Files | {m['source_files']} |")
        L.append(f"| Total LOC | {m['total_loc']:,} |")
        for lang, loc in sorted(m["lines_of_code"].items(), key=lambda x: -x[1]):
            L.append(f"| LOC ({lang}) | {loc:,} |")
        L.append(f"| Entry Points | {len(a['entry_points'])} |")
        L.append(f"| Config Files | {len(a['config_files'])} |")
        L.append(f"| Architecture Style | {arch.get('primary_style', 'unknown')} |")
        L.append(f"| Security Risk Score | {sec.get('risk_score', 0)}/100 |")
        L.append(f"| Test-to-Code Ratio | {ta.get('test_to_code_ratio_pct', 0)}% |")
        L.append(f"| Avg CC | {cc.get('avg_complexity', 0)} |")
        L.append(f"| Comment Density | {cd.get('overall_density_pct', 0)}% |")
        L.append(f"| Duplicate Blocks | {dup.get('total_duplicate_blocks', 0)} |")
        L.append(f"| Total Debt Markers | {sum(a['debt'].values())} |")
        L.append("")

        # File type breakdown
        L.append("## File Type Distribution\n")
        L.append("| Extension | Count |")
        L.append("|-----------|-------|")
        for ext, count in list(s["file_counts_by_ext"].items())[:20]:
            L.append(f"| `{ext}` | {count} |")
        L.append("")

        # Directory listing
        L.append("## Top-Level Directories\n")
        for d in s["directories"][:30]:
            L.append(f"- `{d}`")
        L.append("")

        # Entry points
        L.append("## Entry Points\n")
        for ep in a["entry_points"]:
            L.append(f"- `{ep}`")
        L.append("")

        # Config files
        L.append("## Configuration Files\n")
        for cf in a["config_files"]:
            L.append(f"- `{cf}`")
        L.append("")

        # Gitingest summary
        gi = a.get("gitingest", {})
        if gi:
            L.append("## Source Dump Summary\n")
            L.append(f"- **Total Characters**: {gi.get('total_chars', 0):,}")
            L.append(f"- **Estimated Words**: {gi.get('estimated_words', 0):,}")
            L.append(f"- **Files Included**: {gi.get('files_included', 0)}")
            L.append(f"- **Files Skipped (cap)**: {gi.get('files_skipped_cap', 0)}")
            if gi.get("dump_path"):
                L.append(f"- **Dump Path**: `{gi['dump_path']}`")
            L.append("")

        return "\n".join(L)

    def report_architecture(self, a: Dict) -> str:
        """01_architecture_analysis.md — patterns, style, coupling."""
        name = a["name"]
        L = [f"# Architecture Analysis: {name}\n"]

        arch_style = a.get("architecture_style", {})
        L.append("## Architecture Style Classification\n")
        L.append(f"**Primary Style**: {arch_style.get('primary_style', 'unknown')}")
        L.append(f"**Confidence Score**: {arch_style.get('confidence_score', 0)}\n")

        if arch_style.get("all_indicators"):
            L.append("### Style Indicators\n")
            L.append("| Style | Score |")
            L.append("|-------|-------|")
            for style, score in sorted(arch_style["all_indicators"].items(), key=lambda x: -x[1]):
                L.append(f"| {style} | {score} |")
            L.append("")

        L.append("## Architecture Patterns (Ripgrep Counts)\n")
        L.append("| Pattern | Occurrences |")
        L.append("|---------|-------------|")
        for pat, count in sorted(a["architecture"].items(), key=lambda x: -x[1]):
            L.append(f"| {pat} | {count} |")
        L.append("")

        drg = a.get("deep_ripgrep", {})
        if drg.get("detected_frameworks"):
            L.append("## Detected Frameworks & Libraries\n")
            L.append("| Framework | Occurrences |")
            L.append("|-----------|-------------|")
            for fw, count in sorted(drg["detected_frameworks"].items(), key=lambda x: -x[1]):
                L.append(f"| {fw} | {count} |")
            L.append("")

        if drg.get("hotspots"):
            L.append("## Architecture Hotspots\n")
            L.append("| Rank | File | Pattern Hits |")
            L.append("|------|------|-------------|")
            for i, h in enumerate(drg["hotspots"][:20], 1):
                L.append(f"| {i} | `{h['file']}` | {h['pattern_hits']} |")
            L.append("")

        if drg.get("pattern_file_map"):
            L.append("## Pattern-to-File Mapping\n")
            for pat_name, pat_files in drg["pattern_file_map"].items():
                if pat_files:
                    L.append(f"\n### {pat_name} ({len(pat_files)} files)\n")
                    for pf in pat_files[:10]:
                        L.append(f"- `{pf}`")
            L.append("")

        mc = a.get("module_coupling", {})
        if mc:
            L.append("## Module Coupling Analysis\n")
            L.append(f"- **Modules Analyzed**: {mc.get('module_count', 0)}")
            L.append(f"- **Average Instability**: {mc.get('avg_instability', 0)}\n")

            if mc.get("coupling_table"):
                L.append("### Coupling Table\n")
                L.append("| Module | Afferent (Ca) | Efferent (Ce) | Instability (I) |")
                L.append("|--------|:------------:|:------------:|:--------------:|")
                for c in mc["coupling_table"][:50]:
                    L.append(f"| {c['module']} | {c['afferent_coupling']} | "
                             f"{c['efferent_coupling']} | {c['instability']} |")
                L.append("")

            L.append("### Instability Interpretation\n")
            L.append("- **I = 0**: Maximally stable (many dependents, few dependencies)")
            L.append("- **I = 1**: Maximally unstable (few dependents, many dependencies)")
            L.append("- **I ≈ 0.5**: Balanced\n")

            if mc.get("most_depended_on"):
                L.append("### Most Depended-On Modules (Highest Afferent)\n")
                for c in mc["most_depended_on"][:10]:
                    L.append(f"- **{c['module']}** — Ca={c['afferent_coupling']}, "
                             f"imported by: {', '.join(c.get('afferent_sources', [])[:5])}")
                L.append("")

            if mc.get("most_dependent"):
                L.append("### Most Dependent Modules (Highest Efferent)\n")
                for c in mc["most_dependent"][:10]:
                    L.append(f"- **{c['module']}** — Ce={c['efferent_coupling']}, "
                             f"imports: {', '.join(c.get('efferent_targets', [])[:5])}")
                L.append("")

        return "\n".join(L)

    def report_metrics_complexity(self, a: Dict) -> str:
        """02_code_metrics_complexity.md — LOC, CC, comment density."""
        name = a["name"]
        L = [f"# Code Metrics & Complexity: {name}\n"]

        m = a["metrics"]
        L.append("## Lines of Code by Language\n")
        L.append("| Language | LOC | % of Total |")
        L.append("|----------|----:|:----------:|")
        for lang, loc in sorted(m["lines_of_code"].items(), key=lambda x: -x[1]):
            pct = round(loc / m["total_loc"] * 100, 1) if m["total_loc"] else 0
            L.append(f"| {lang} | {loc:,} | {pct}% |")
        L.append(f"| **Total** | **{m['total_loc']:,}** | **100%** |")
        L.append("")

        cc = a.get("cyclomatic_complexity", {})
        if cc:
            L.append("## Cyclomatic Complexity Analysis\n")
            L.append(f"- **Functions Analyzed**: {cc.get('total_functions_analyzed', 0)}")
            L.append(f"- **Average CC**: {cc.get('avg_complexity', 0)}")
            L.append(f"- **Median CC**: {cc.get('median_complexity', 0)}")
            L.append(f"- **Max CC**: {cc.get('max_complexity', 0)}\n")

            if cc.get("distribution"):
                L.append("### Complexity Distribution\n")
                L.append("| Range | Count | Interpretation |")
                L.append("|-------|------:|---------------|")
                for range_name, count in cc["distribution"].items():
                    L.append(f"| {range_name} | {count} | |")
                L.append("")

            if cc.get("top_complex_functions"):
                L.append("### Most Complex Functions\n")
                L.append("| Rank | Function | File | Line | CC | Async |")
                L.append("|------|----------|------|-----:|---:|:-----:|")
                for i, fn in enumerate(cc["top_complex_functions"][:100], 1):
                    async_mark = "✓" if fn.get("is_async") else ""
                    L.append(f"| {i} | `{fn['name']}` | `{fn['file']}` | "
                             f"{fn['line']} | **{fn['complexity']}** | {async_mark} |")
                L.append("")

            if cc.get("file_complexity"):
                L.append("### File-Level Complexity\n")
                L.append("| File | Max CC | Avg CC | Functions | Total CC |")
                L.append("|------|-------:|-------:|----------:|---------:|")
                for fpath, data in cc["file_complexity"][:50]:
                    L.append(f"| `{fpath}` | {data['max_cc']} | {data['avg_cc']} | "
                             f"{data['func_count']} | {data['total_cc']} |")
                L.append("")

        cd = a.get("comment_density", {})
        if cd:
            L.append("## Comment Density Analysis\n")
            L.append(f"- **Overall Density**: {cd.get('overall_density_pct', 0)}%")
            L.append(f"- **Total Code Lines**: {cd.get('total_code_lines', 0):,}")
            L.append(f"- **Total Comment Lines**: {cd.get('total_comment_lines', 0):,}")
            L.append(f"- **Total Blank Lines**: {cd.get('total_blank_lines', 0):,}")
            L.append(f"- **Files Analyzed**: {cd.get('total_files_analyzed', 0)}\n")

            if cd.get("files_with_most_comments"):
                L.append("### Files With Highest Comment Density\n")
                L.append("| File | Code | Comments | Blank | Density % |")
                L.append("|------|-----:|--------:|------:|----------:|")
                for f in cd["files_with_most_comments"][:30]:
                    L.append(f"| `{f['file']}` | {f['code_lines']} | {f['comment_lines']} | "
                             f"{f['blank_lines']} | {f['comment_density_pct']}% |")
                L.append("")

            if cd.get("zero_comment_files"):
                L.append("### Files With Zero Comments (>20 code lines)\n")
                L.append("| File | Code Lines |")
                L.append("|------|----------:|")
                for f in cd["zero_comment_files"][:30]:
                    L.append(f"| `{f['file']}` | {f['code_lines']} |")
                L.append("")

        hs = a.get("hotspots", {})
        if hs:
            L.append("## File Size Analysis\n")
            L.append(f"- **Files > 500 LOC**: {hs.get('files_over_500_loc', 0)}")
            L.append(f"- **Files > 1000 LOC**: {hs.get('files_over_1000_loc', 0)}")
            L.append(f"- **Median File LOC**: {hs.get('median_file_loc', 0)}\n")

            if hs.get("largest_files"):
                L.append("### Largest Source Files\n")
                L.append("| Rank | File | LOC |")
                L.append("|------|------|----:|")
                for i, lf in enumerate(hs["largest_files"][:15], 1):
                    L.append(f"| {i} | `{lf['file']}` | {lf['loc']:,} |")
                L.append("")

        return "\n".join(L)

    def report_ast_deep(self, a: Dict) -> str:
        """03_ast_deep_analysis.md — classes, functions, inheritance."""
        name = a["name"]
        L = [f"# AST Deep Analysis: {name}\n"]

        ast_d = a.get("ast", {})
        if not ast_d:
            L.append("*No Python AST data available.*\n")
            return "\n".join(L)

        L.append(f"- **Python Files Parsed**: {ast_d.get('total_python_files', 0)}")
        L.append(f"- **Total Classes**: {ast_d.get('total_classes', 0)}")
        L.append(f"- **Total Functions**: {ast_d.get('total_functions', 0)}")
        L.append(f"- **Async Functions**: {ast_d.get('async_function_count', 0)}\n")

        if ast_d.get("classes"):
            L.append("## Class Inventory\n")
            L.append("| Class | File | Line | Methods | Bases | Decorators |")
            L.append("|-------|------|-----:|--------:|-------|------------|")
            for cls in ast_d["classes"][:50]:
                bases_str = ", ".join(cls.get("bases", [])[:3]) or "—"
                decos = ", ".join(cls.get("decorators", [])[:3]) or "—"
                L.append(f"| `{cls['name']}` | `{cls['file']}` | {cls['line']} | "
                         f"{cls['method_count']} | {bases_str} | {decos} |")
            L.append("")

            L.append("### Method Details (Top Classes)\n")
            for cls in ast_d.get("largest_classes", [])[:10]:
                L.append(f"\n#### `{cls['name']}` ({cls['file']}:{cls['line']})\n")
                L.append(f"**Bases**: {', '.join(cls.get('bases', [])) or 'None'}")
                L.append(f"**Methods** ({cls['method_count']}):\n")
                for meth in cls.get("methods", [])[:20]:
                    L.append(f"- `{meth}()`")
                L.append("")

        if ast_d.get("inheritance_tree"):
            L.append("## Inheritance Tree\n")
            L.append("| Base Class | Children | Count |")
            L.append("|------------|----------|------:|")
            for base, children in sorted(ast_d["inheritance_tree"].items(), key=lambda x: -len(x[1]))[:30]:
                L.append(f"| `{base}` | {', '.join(children[:8])} | {len(children)} |")
            L.append("")

        if ast_d.get("top_imports"):
            L.append("## Import Graph (Most-Imported Modules)\n")
            L.append("| Rank | Module | Importing Files |")
            L.append("|------|--------|---------------:|")
            for i, (mod, count) in enumerate(ast_d["top_imports"][:30], 1):
                L.append(f"| {i} | `{mod}` | {count} |")
            L.append("")

        return "\n".join(L)

    def report_design_patterns(self, a: Dict) -> str:
        """04_design_patterns.md."""
        name = a["name"]
        L = [f"# Design Patterns: {name}\n"]

        dp = a.get("design_patterns", {})
        if not dp:
            L.append("*No design patterns detected.*\n")
            return "\n".join(L)

        L.append(f"**Patterns Detected**: {len(dp)}\n")
        L.append("## Pattern Summary\n")
        L.append("| Pattern | Files | Significance |")
        L.append("|---------|------:|-------------|")
        sig_map = {
            "Singleton": "State management, global access",
            "Factory": "Object creation abstraction",
            "Observer/Event": "Decoupled communication",
            "Strategy": "Interchangeable algorithms",
            "Decorator": "Behavior modification",
            "Plugin/Extension": "Extensibility architecture",
            "Middleware": "Request/response processing chain",
        }
        for pname, pfiles in sorted(dp.items(), key=lambda x: -len(x[1])):
            L.append(f"| {pname} | {len(pfiles)} | {sig_map.get(pname, '—')} |")
        L.append("")

        for pname, pfiles in sorted(dp.items(), key=lambda x: -len(x[1])):
            L.append(f"## {pname}\n")
            L.append(f"**Files**: {len(pfiles)}\n")
            for pf in pfiles[:20]:
                L.append(f"- `{pf}`")
            L.append("")

        return "\n".join(L)

    def report_api_dependencies(self, a: Dict) -> str:
        """05_api_surface_dependencies.md."""
        name = a["name"]
        L = [f"# API Surface & Dependencies: {name}\n"]

        api = a.get("api_surface", {})
        if api:
            L.append("## API Surface Overview\n")
            L.append(f"- **Total API Surface**: {api.get('total_api_surface', 0)} endpoints/tools\n")
            for section, key in [("HTTP Route Files", "http_route_files"),
                                 ("CLI Command Files", "cli_files"),
                                 ("Tool Registrations", "tool_registrations")]:
                if api.get(key):
                    L.append(f"### {section}\n")
                    for f in api[key][:20]:
                        L.append(f"- `{f}`")
                    L.append("")

        L.append("## Dependencies\n")
        for ecosystem, deps in a["dependencies"].items():
            L.append(f"### {ecosystem}\n")
            if isinstance(deps, list):
                L.append(f"**Count**: {len(deps)}\n")
                L.append("| # | Package |")
                L.append("|---|---------|")
                for i, d in enumerate(deps[:50], 1):
                    L.append(f"| {i} | `{d}` |")
            L.append("")

        return "\n".join(L)

    def report_quality_debt(self, a: Dict) -> str:
        """06_code_quality_debt.md — debt, security, duplication, tests."""
        name = a["name"]
        L = [f"# Code Quality & Technical Debt: {name}\n"]

        # Debt markers
        L.append("## Technical Debt Markers\n")
        total_debt = sum(a["debt"].values())
        L.append(f"**Total Markers**: {total_debt}\n")
        L.append("| Marker | Count |")
        L.append("|--------|------:|")
        for marker, count in sorted(a["debt"].items(), key=lambda x: -x[1]):
            L.append(f"| {marker} | {count} |")
        L.append("")

        # Security
        sec = a.get("security", {})
        if sec:
            L.append("## Security Analysis\n")
            L.append(f"- **Risk Score**: {sec.get('risk_score', 0)}/100")
            L.append(f"- **Total Findings**: {sec.get('total_findings', 0)}")
            L.append(f"- **Patterns Checked**: {sec.get('patterns_checked', 0)}\n")

            sev = sec.get("severity_counts", {})
            L.append("### Severity Breakdown\n")
            L.append("| Severity | Count |")
            L.append("|----------|------:|")
            sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            emoji_map = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
            for s, c in sorted(sev.items(), key=lambda x: sev_order.get(x[0], 99)):
                L.append(f"| {emoji_map.get(s, '⚪')} {s.upper()} | {c} |")
            L.append("")

            if sec.get("findings"):
                L.append("### Findings Detail\n")
                sev_levels = {
                    "eval_usage": "CRITICAL", "exec_usage": "CRITICAL",
                    "hardcoded_password": "CRITICAL", "hardcoded_secret": "CRITICAL",
                    "sql_injection_risk": "CRITICAL",
                    "subprocess_shell": "HIGH", "pickle_load": "HIGH",
                    "yaml_unsafe": "HIGH", "hardcoded_credentials": "HIGH",
                    "debug_enabled": "MEDIUM", "cors_wildcard": "MEDIUM",
                    "temp_file_risk": "LOW",
                }
                for finding_name, files in sec["findings"].items():
                    sl = sev_levels.get(finding_name, "MEDIUM")
                    L.append(f"\n#### {finding_name} [{sl}] — {len(files)} files\n")
                    for f in files[:15]:
                        L.append(f"- `{f}`")
                L.append("")

        # Duplication
        dup = a.get("duplication", {})
        if dup:
            L.append("## Code Duplication Analysis\n")
            L.append(f"- **Block Size**: {dup.get('block_size', 5)} consecutive lines")
            L.append(f"- **Total Duplicate Blocks**: {dup.get('total_duplicate_blocks', 0)}")
            L.append(f"- **Unique Patterns**: {dup.get('unique_duplicate_patterns', 0)}")
            L.append(f"- **Cross-File Duplicates**: {dup.get('cross_file_duplicates', 0)}\n")

            if dup.get("top_cross_file_duplicates"):
                L.append("### Cross-File Duplicates (Most Critical)\n")
                L.append("| Pattern | Occurrences | Files | Preview |")
                L.append("|---------|:----------:|------:|---------|")
                for d in dup["top_cross_file_duplicates"][:20]:
                    L.append(f"| `{d['hash']}` | {d['occurrences']} | "
                             f"{d['unique_files']} | `{d['preview'][:40]}` |")
                L.append("")

                for d in dup["top_cross_file_duplicates"][:10]:
                    L.append(f"\n#### Duplicate `{d['hash']}` — {d['occurrences']} occurrences\n")
                    for loc in d.get("locations", [])[:6]:
                        L.append(f"- `{loc['file']}` line {loc['start_line']}: `{loc['preview'][:60]}`")
                L.append("")

            if dup.get("top_duplicates"):
                L.append("### All Top Duplicate Patterns\n")
                L.append("| Hash | Occurrences | Unique Files | Preview |")
                L.append("|------|:----------:|:-----------:|---------|")
                for d in dup["top_duplicates"][:40]:
                    L.append(f"| `{d['hash']}` | {d['occurrences']} | "
                             f"{d['unique_files']} | `{d['preview'][:40]}` |")
                L.append("")

        # Test analysis
        ta = a.get("test_analysis", {})
        if ta:
            L.append("## Test Analysis\n")
            L.append(f"- **Test Files**: {ta.get('test_file_count', 0)}")
            L.append(f"- **Source Files**: {ta.get('source_file_count', 0)}")
            L.append(f"- **Test LOC**: {ta.get('test_loc', 0):,}")
            L.append(f"- **Source LOC**: {ta.get('source_loc', 0):,}")
            L.append(f"- **Test-to-Code Ratio**: {ta.get('test_to_code_ratio_pct', 0)}%")
            L.append(f"- **Test Functions**: {ta.get('test_function_count', 0)}")
            L.append(f"- **Assertions**: {ta.get('assertion_count', 0)}\n")

            if ta.get("test_frameworks_detected"):
                L.append("### Test Frameworks Detected\n")
                for fw in ta["test_frameworks_detected"]:
                    L.append(f"- {fw}")
                L.append("")

            if ta.get("test_files"):
                L.append("### Test File Inventory\n")
                L.append("| File | LOC |")
                L.append("|------|----:|")
                for tf in ta["test_files"][:50]:
                    L.append(f"| `{tf['file']}` | {tf['loc']} |")
                L.append("")

        return "\n".join(L)

    def report_call_graph_hotspots(self, a: Dict) -> str:
        """07_call_graph_hotspots.md."""
        name = a["name"]
        L = [f"# Call Graph & Hotspots: {name}\n"]

        cg = a.get("call_graph", {})
        if cg:
            L.append("## Call Graph Analysis\n")
            L.append(f"- **Unique Call Targets**: {cg.get('total_unique_calls', 0)}\n")

            if cg.get("most_called_functions"):
                L.append("### Most-Called Functions\n")
                L.append("| Rank | Function | Call Count | Defined In |")
                L.append("|------|----------|:----------:|------------|")
                for i, fn in enumerate(cg["most_called_functions"][:25], 1):
                    L.append(f"| {i} | `{fn['name']}` | {fn['call_count']} | "
                             f"`{fn['defined_in']}` |")
                L.append("")

        drg = a.get("deep_ripgrep", {})
        if drg.get("hotspots"):
            L.append("## Architecture Hotspot Files\n")
            L.append("| Rank | File | Pattern Hits |")
            L.append("|------|------|:-----------:|")
            for i, h in enumerate(drg["hotspots"][:20], 1):
                L.append(f"| {i} | `{h['file']}` | {h['pattern_hits']} |")
            L.append("")

        hs = a.get("hotspots", {})
        if hs and hs.get("largest_files"):
            L.append("## Largest Source Files (Complexity Hotspots)\n")
            L.append("| Rank | File | LOC |")
            L.append("|------|------|----:|")
            for i, lf in enumerate(hs["largest_files"][:15], 1):
                L.append(f"| {i} | `{lf['file']}` | {lf['loc']:,} |")
            L.append("")

        return "\n".join(L)
