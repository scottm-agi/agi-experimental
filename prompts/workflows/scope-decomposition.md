---
description: Decompose large technical scopes into well-structured Forgejo issues across repos
---

# Scope Decomposition Workflow

This workflow takes a large technical scope and breaks it into structured, actionable Forgejo issues across relevant repositories.

## Prerequisites
- Access to Forgejo MCP tools
- Clear understanding of the technical scope to decompose
- Knowledge of which repositories are involved (e.g., agix, agix-devdocs, frontend)

## Steps

### 1. Analyze the Scope
Read and understand the full scope document/description:
1. **Identify components**: List all systems/modules affected
2. **Map to repos**: Determine which repo owns each component
3. **Identify dependencies**: Note which changes depend on others

### 2. Classify by Layer
Categorize each piece of work:

| Layer | Repository | Examples |
|-------|-----------|----------|
| **Agent Core** | agix | Python extensions, tools, helpers |
| **WebUI/Frontend** | agix | JavaScript, HTML, CSS in webui/ |
| **Backend API** | agix | API endpoints, settings |
| **Marketing/Landing** | agix-marketingsite-frontend | Next.js frontend |
| **DevOps** | agix | Docker config, deployment |
| **Documentation** | agix-devdocs | ADRs, analysis, tests |

### 3. Create Parent Issue
Create a tracking/epic issue in the primary repo:
```
Title: [EPIC] <Scope Description>
Body: 
## Overview
<Brief description of the full scope>

## Sub-Issues
- [ ] #<num> - <component 1>
- [ ] #<num> - <component 2>
...

## Dependencies
<Component B depends on Component A>
```

### 4. Create Sub-Issues
For each component, create a focused issue:
1. **Title**: Clear, actionable (e.g., "Add X support to Y module")
2. **Body**: 
   - What needs to change
   - Which files are affected
   - Acceptance criteria
   - Dependencies (link to parent and blocking issues)
3. **Labels**: Add appropriate labels (bug, enhancement, PFR)
4. **Milestone**: Assign to relevant milestone if exists

### 5. Link Dependencies
Use Forgejo's dependency system:
- Add blocking relationships between issues
- Reference parent epic in each sub-issue
- Cross-reference between repos where applicable

### 6. Prioritize
Order issues by:
1. **Blockers first**: Issues that others depend on
2. **Foundation before features**: Core/API before UI
3. **Risk first**: High-uncertainty items early for fast feedback

### 7. Verify Completeness
Checklist:
- [ ] Every component in scope has at least one issue
- [ ] Dependencies are linked
- [ ] Each issue has acceptance criteria
- [ ] Parent epic tracks all sub-issues
- [ ] Cross-repo references are in place

## Tips
- Keep issues small (1-2 days of work max)
- Use consistent labeling across repos
- Include "Definition of Done" in acceptance criteria
- Reference the original scope document in the epic
- **MANDATORY**: Before embedding any GitHub file path or image URL in issue bodies, call `verify_github_path` to sanitize and verify the path. The canonical mockup path is `docs/mockups/{filename}` — never use doubled segments like `docs/main/docs/mockups/`.

