## Active project
Path: {{project_path}}
Title: {{project_name}}
Description: {{project_description}}

### ⚠️ CRITICAL: File Path Requirements
ALL file operations MUST use paths starting with `{{project_path}}/`.
NEVER write, read, or modify files outside `{{project_path}}`.

**Correct** (every file path starts with your project directory):
- `{{project_path}}/src/app/page.jsx` ✅
- `{{project_path}}/public/logo.png` ✅
- `{{project_path}}/package.json` ✅

**WRONG** (bare framework paths — these will be BLOCKED):
- `/agix/src/app/page.jsx` ❌
- `/agix/public/logo.png` ❌
- `/agix/package.json` ❌

### Essential Project Guidelines
- Always work inside `{{project_path}}` directory — this is your project sandbox
- Do not rename project directory or change meta files in .agix.proj folder
- If code accidentally creates files outside `{{project_path}}`, move them back immediately

{{project_instructions}}