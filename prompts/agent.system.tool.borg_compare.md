## Tool: borg_compare

World-class codebase forensic analysis and pair-wise comparison tool ("Borg").
Performs deep structural analysis using Python AST parsing, ripgrep pattern matching,
call graph construction, design pattern detection, framework fingerprinting,
cyclomatic complexity scoring, security auditing, test coverage analysis, module
coupling metrics, duplication detection, and gitingest-style full-text dumps.
Produces comprehensive multi-file markdown reports for codebase porting, assimilation,
and architectural comparison decisions.

### Actions

- **`analyze`** — Forensic analysis of a single codebase (all 21 dimensions).
- **`compare`** — Pair-wise forensic comparison of two codebases.

### Arguments

```json
{
    "source_path": "/path/to/source/codebase",
    "target_path": "/path/to/target/codebase",
    "action": "compare",
    "output_dir": "/path/to/save/reports",
    "report_depth": "comprehensive"
}
```

- **`source_path`** (required): Absolute path to the source codebase.
- **`target_path`** (required for `compare`): Absolute path to the target codebase.
- **`action`** (optional, default: `compare`): `analyze` or `compare`.
- **`output_dir`** (required for comprehensive): Directory to save report files + gitingest dumps.
- **`report_depth`** (optional, default: `comprehensive`): `comprehensive` (multi-file, 5K+ lines) or `summary` (single-file legacy).

### Report Output (Comprehensive Mode)

When `report_depth` is `comprehensive`, Borg writes **8 sub-reports + index** to `output_dir/`:

```
output_dir/
├── index.md                          # Master index linking all sub-reports
├── 00_executive_summary.md           # Scale overview + at-a-glance tables
├── 01_architecture_analysis.md       # Patterns, style classification, module coupling
├── 02_code_metrics_complexity.md     # LOC, cyclomatic complexity, comment density
├── 03_ast_deep_analysis.md           # Classes, inheritance, imports, function sigs
├── 04_design_patterns.md             # Singleton, Factory, Observer, etc.
├── 05_api_surface_dependencies.md    # HTTP routes, CLI, tools, dependencies
├── 06_code_quality_debt.md           # Debt, security, duplication, test analysis
├── 07_call_graph_hotspots.md         # Call graph, architecture hotspots
├── 08_strategic_comparison.md        # (compare only) Porting/migration analysis
└── *_ingest.txt                      # Raw source dumps
```

For `compare` action, each codebase gets its own prefixed set of sub-reports (`source_*`, `target_*`)
plus a shared `08_strategic_comparison.md`.

### Forensic Analysis Dimensions (21 Total)

**Original 15:**
1. **Project Structure** — Directory layout, file inventory by extension
2. **Code Metrics** — LOC by language, source file counts
3. **AST Analysis** — Class hierarchies, inheritance trees, import graphs, function signatures
4. **Deep Ripgrep** — Pattern hotspot detection, framework fingerprinting
5. **Complexity Hotspots** — Largest files, files >500/>1000 LOC
6. **Gitingest Dump** — Full-text source concatenation for LLM ingestion
7. **Design Patterns** — Singleton, Factory, Observer, Strategy, Decorator, Plugin, Middleware
8. **Call Graph** — AST-based call frequency analysis
9. **API Surface** — HTTP routes, CLI commands, tool registrations
10. **Architecture Style** — Auto-classification with confidence scoring
11. **Dependencies** — Python, Node.js, Go, Rust ecosystem parsing
12. **Technical Debt** — TODO, FIXME, HACK, XXX marker counts
13. **Entry Points & Configuration** — Main files, Docker, env configs

**New Gold-Standard Dimensions:**
14. **Cyclomatic Complexity** — Per-function CC via AST decision-point counting, distribution analysis, file-level aggregation
15. **Comment Density** — Comment-to-code ratio per file, overall %, zero-comment file detection
16. **Code Duplication** — Hash-based repeated block detection, cross-file duplicate analysis
17. **Security Markers** — eval/exec/hardcoded secrets/SQL injection/pickle/CORS scanning with CVSS-style risk scoring (0-100)
18. **Test Analysis** — Test file detection, test-to-code ratio, framework detection, assertion counts
19. **Module Coupling** — Afferent/efferent coupling per module, instability index (Robert C. Martin)

**Derived:**
20. **Architecture Classification** — Composite scoring from all collected evidence
21. **Strategic Assessment** — (compare mode) Migration effort, framework overlap, porting roadmap

### Example Usage

```json
{
    "tool_name": "borg_compare",
    "tool_args": {
        "source_path": "/agix/usr/projects/agi-experimental-upstream",
        "target_path": "/agix",
        "action": "compare",
        "output_dir": "/agix/tmp/borg-reports",
        "report_depth": "comprehensive"
    }
}
```

### When To Use

- **Architecture reviews**: Analyze a codebase before making architectural decisions
- **Porting/migration planning**: Compare source and target codebases for assimilation
- **Code quality audits**: Security scanning, duplication detection, complexity analysis
- **Onboarding**: Generate comprehensive documentation of an unfamiliar codebase
- **Technical debt assessment**: Identify hotspots, debt markers, untested code

### Post-Processing: LLM Iteration (MANDATORY)

The static reports from `borg_compare` are **Phase 1 only**. After the tool completes, you MUST perform Phase 2 — LLM-powered semantic analysis:

1. **Read each sub-report** via `code_execution_tool`: `cat output_dir/*.md`
2. **Read gitingest dumps in chunks** (5000 lines at a time):
   ```bash
   sed -n '1,5000p' output_dir/*_ingest.txt
   sed -n '5001,10000p' output_dir/*_ingest.txt
   # Continue until EOF
   ```
3. **For each chunk**, analyze and extract:
   - Feature inventory and capability mapping
   - Architecture pattern identification (beyond static classification)
   - Integration points and dependency narratives
   - Code quality narrative (explain WHY, not just metrics)
   - Security posture with risk narratives
4. **Write semantic analysis** to `output_dir/09_llm_semantic_analysis.md`
5. **Write feature inventory** to `output_dir/10_feature_inventory.md`
6. **Continue reading** chunks until entire ingest file is processed

> **DO NOT** skip this step. The ingest files exist specifically to be consumed by the LLM for deep semantic understanding. The static reports are just structured data — the real value is in the LLM-powered narrative synthesis.
