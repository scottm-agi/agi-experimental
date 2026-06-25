### content_quality_gate
Score the quality of a deliverable document across 5 dimensions:
**structure**, **depth**, **evidence**, **actionability**, and **specificity**.

Use BEFORE final delivery to ensure content meets executive standards.

**Grading scale:**
- Grade **A** (85+) → Publication-ready, exceptional quality
- Grade **B** (70+) → Acceptable, meets professional standards
- Grade **C** (55+) → Needs improvement in weak dimensions
- Grade **D** (<55) → Requires significant rework

**Parameters:**
- `content` (required): The full markdown content to score.
- `doc_type` (optional): Document type for context (e.g., "competitive_analysis", "account_plan", "playbook", "campaign_brief"). Default: "general"

**Returns:** JSON with total score, grade, pass/fail, per-dimension breakdown, issues, and recommendations.

**Usage pattern:**
```json
{
    "tool_name": "content_quality_gate",
    "tool_args": {
        "content": "<full deliverable content>",
        "doc_type": "account_plan"
    }
}
```

**When to use:**
- After any specialist agent produces a deliverable
- Before accepting content-writer synthesis as final
- When rework is needed — feed the dimension scores back to the specialist

**Quality Rework Protocol (Otter Evaluate-Iterate):**
1. If grade < B → send specialist the EXACT dimension scores + recommendations
2. If still < B after first rework → add "FINAL ATTEMPT — focus on weakest 2 dimensions"
3. After 2 reworks → accept with `quality_flag: "below-threshold"` in YAML frontmatter
