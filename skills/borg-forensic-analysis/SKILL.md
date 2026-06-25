---
name: "borg-forensic-analysis"
description: "Codebase forensic analysis and comparison using the Borg engine. Analyzes code architecture, complexity, patterns, and generates comprehensive reports. Use when asked to analyze, compare, port, or forensically examine codebases."
version: "1.0.0"
author: "AGIX Team"
tags: ["forensics", "analysis", "comparison", "architecture", "porting", "codebase"]
trigger_patterns:
  - "analyze codebase"
  - "compare codebases"
  - "codebase forensics"
  - "code forensics"
  - "borg compare"
  - "borg analyze"
  - "forensic analysis"
  - "codebase comparison"
  - "port codebase"
  - "assimilate codebase"
  - "code architecture analysis"
  - "deep code analysis"
required_agents:
  - multiagentdev    # Orchestrator — decomposes and manages the pipeline
  - architect        # Synthesizes reports into executive narratives
  - code             # Runs borg_compare tool and iterates over ingest files
---

# Borg Forensic Analysis Skill

High-fidelity, multi-phase codebase forensic analysis pipeline.
Produces 20+ structured reports (AST, complexity, patterns, architecture, quality) plus LLM-enhanced semantic narratives.

## Orchestration

This skill requires **multiagentdev** (orchestrator) to coordinate the pipeline.
The orchestrator delegates to **code** and **architect** agents as needed.

## Pipeline Overview

```
User Request
    ↓
[multiagentdev] Orchestrator — decomposes the request
    ↓
[code] Phase 1 — runs borg_compare tool (static analysis)
    ↓
[code] Phase 2 — iterates over gitingest dumps via LLM
    ↓
[architect] Phase 3 — synthesizes all reports into executive narrative
    ↓
[multiagentdev] Delivers final results to user
```

## Phase 1: Static Forensic Scan (Code Agent)

The orchestrator delegates to a **code** agent to run the `borg_compare` tool:

### Single Codebase Analysis
```json
{
    "tool_name": "borg_compare",
    "tool_args": {
        "source_path": "/agix/usr/projects/<repo>",
        "action": "analyze",
        "output_dir": "/agix/tmp/borg-<name>",
        "report_depth": "comprehensive"
    }
}
```

### Pair-wise Comparison
```json
{
    "tool_name": "borg_compare",
    "tool_args": {
        "source_path": "/agix/usr/projects/<source-repo>",
        "target_path": "/agix/usr/projects/<target-repo>",
        "action": "compare",
        "output_dir": "/agix/tmp/borg-<name>",
        "report_depth": "comprehensive"
    }
}
```

### Phase 1 Output
The tool generates 13+ report files in `output_dir`:
- `source_00_executive_summary.md` — High-level overview
- `source_01_directory_structure.md` — File tree analysis
- `source_02_complexity_analysis.md` — Cyclomatic complexity
- `source_03_ast_deep_analysis.md` — AST structural analysis
- `source_04_dependency_analysis.md` — Dependency graph
- `source_05_architecture_patterns.md` — Design patterns detected
- `source_06_code_quality_debt.md` — Quality & tech debt
- `source_07_naming_conventions.md` — Naming analysis
- `source_gitingest_dump.txt` — Full concatenated source (for Phase 2)
- `08_strategic_comparison.md` — Delta analysis (compare mode only)

## Phase 2: LLM-Powered Semantic Analysis (Code Agents)

After Phase 1, the orchestrator delegates to **code** agents to read and analyze the gitingest dumps. These are large files that must be read in chunks:

### Subtask A: Read Sub-Reports
- Read each `.md` report from `output_dir`
- Produce `09_llm_semantic_analysis.md` with semantic narrative for each section

### Subtask B: Iterate Source Ingest (5K-line chunks)
- Read `source_gitingest_dump.txt` in 5,000-line chunks
- For each chunk: identify features, architectural patterns, security considerations
- Write to `10_source_feature_inventory.md`

### Subtask C: Iterate Target Ingest (compare mode only)
- Read `target_gitingest_dump.txt` in 5,000-line chunks
- Same analysis as Subtask B
- Write to `11_target_feature_inventory.md`

### Delegation Pattern
```json
{
    "tool_name": "call_subordinate_batch",
    "tool_args": {
        "tasks": [
            {"id": "reports", "message": "Read all .md reports in <output_dir> and produce 09_llm_semantic_analysis.md", "profile": "code"},
            {"id": "source", "message": "Read <output_dir>/source_gitingest_dump.txt in 5K-line chunks. For each chunk identify features, patterns, quality. Write 10_source_feature_inventory.md", "profile": "code", "dependencies": ["reports"]},
            {"id": "target", "message": "Read <output_dir>/target_gitingest_dump.txt in 5K-line chunks. For each chunk identify features, patterns, quality. Write 11_target_feature_inventory.md", "profile": "code", "dependencies": ["reports"]}
        ],
        "execution_mode": "wave",
        "aggregate_results": true
    }
}
```

## Phase 3: Executive Synthesis (Architect Agent)

After Phase 2, the orchestrator delegates to an **architect** agent to synthesize everything:

```json
{
    "tool_name": "call_subordinate",
    "tool_args": {
        "profile": "architect",
        "message": "Read ALL reports in <output_dir> (00-11). Synthesize into 12_executive_narrative.md — a comprehensive architectural comparison with strategic recommendations for porting, risk assessment, and prioritized action items.",
        "reset": "true"
    }
}
```

### Phase 3 Output: `12_executive_narrative.md`
- Executive summary with LOC/scale metrics
- Architecture comparison (source vs target)
- Feature parity analysis
- Risk assessment for porting
- Prioritized action items
- Strategic recommendations

## Orchestrator Checklist

When orchestrating a Borg pipeline, the multiagentdev agent should track:

- [ ] Repos cloned/accessible at expected paths
- [ ] Phase 1: borg_compare ran successfully, output_dir has reports
- [ ] Phase 2A: Semantic analysis of sub-reports complete
- [ ] Phase 2B: Source ingest iteration complete
- [ ] Phase 2C: Target ingest iteration complete (compare mode)
- [ ] Phase 3: Executive narrative synthesized
- [ ] Final summary delivered to user

## Tips

- Always use `report_depth: comprehensive` for full analysis
- For repos that need cloning, delegate to a code agent first: `git clone <url> /agix/usr/projects/<name>`
- The ingest files can be 100K+ lines — always read in 5K-line chunks, never try to load all at once
- If Phase 1 fails, check that source/target paths exist and are valid git repos
