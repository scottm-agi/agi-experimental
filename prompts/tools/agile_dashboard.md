# agile_dashboard

## Goal
Generate a professional, Jira-style agile dashboard for a repository using Forgejo metrics, Mermaid diagrams, and 7 "killer" gadgets.

## Parameters
- `action`: The action to perform. Default: `generate`.
- `repo`: Optional `owner/repo` string to override the default repository.
- `output_dir`: Optional directory to save the report. Defaults to `research/agile_dashboard`.

## Description
This tool analyzes the repository's issues (open and closed) to produce a comprehensive agile report in Markdown format. It incorporates 7 key gadgets:
1. **Sprint Health**: Visual overview of open vs closed ratios.
2. **Sprint Burndown**: xy-chart tracking progress vs ideal trend.
3. **High-Priority Issues**: Flash list of blockers and flagged items.
4. **Two-Dimensional Statistics**: Tables pivot-mapped by assignee, status, and priority.
5. **Continuous Integration**: Build health summary.
6. **Assigned to Me**: Personalized task list for the current agent context.
7. **Level Up**: High-level velocity and cycle-time insights for project steering.

The resulting dashboard is saved as `dashboard.md` alongside any visual assets.
