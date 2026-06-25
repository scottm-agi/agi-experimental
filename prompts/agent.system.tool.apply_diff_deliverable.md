### apply_diff_deliverable
Apply SEARCH/REPLACE diff blocks to an existing deliverable document.
This tool **only** works on files within the project's `deliverables/` directory and only on document file types — it will **never** touch source code files.

Use this for **multi-block structural changes** to a deliverable (e.g., reorganizing sections, inserting new content between existing paragraphs). For simple text replacements, `replace_in_deliverable` may be simpler.

**Arguments:**
- `path` (required): Path to the deliverable file (relative to `deliverables/` or absolute)
- `diff` (required): One or more SEARCH/REPLACE blocks

**Diff block format:**
```
<<<<<<< SEARCH
(exact text to find in the deliverable)
=======
(replacement text)
>>>>>>> REPLACE
```

**Usage:**
~~~json
{
    "thoughts": ["I need to restructure the competitive analysis section of my deliverable."],
    "tool_name": "apply_diff_deliverable",
    "tool_args": {
        "path": "researcher_20260514_130000.md",
        "diff": "<<<<<<< SEARCH\n## Competitive Analysis\n\nNo competitors identified.\n=======\n## Competitive Analysis\n\n### Direct Competitors\n- Competitor A: Market leader with 35% share\n- Competitor B: Fast-growing challenger\n\n### Indirect Competitors\n- Alternative Solution X\n>>>>>>> REPLACE"
    }
}
~~~

**🔴 IMPORTANT**: This tool is for deliverable documents ONLY. For source code diffs, delegate to a `code` profile subordinate using `apply_diff`.
