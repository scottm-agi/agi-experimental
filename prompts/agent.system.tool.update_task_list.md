## update_task_list

Use `update_task_list` to register and track your work tasks. This is **MANDATORY** — you MUST call this tool at the START of your work to register all planned tasks.

### When to use:
1. **At the START of work** — Register all planned tasks as a checklist
2. **After completing a task** — Update the status to [x] completed
3. **When starting a task** — Update the status to [-] in progress

### Format:
Provide a markdown checklist in the `content` parameter:

```md
[ ] Design database schema
[-] Implement API routes
[x] Create project scaffold
```

### Status markers:
- `[ ]` = pending (not started)
- `[-]` or `[~]` = in progress (actively working)
- `[x]` or `[X]` = completed (done)

### Critical rules:
- **REGISTER FIRST**: You MUST call this tool before doing any work to register your full task list.
- **UPDATE STATUS**: Call this tool again to update task statuses as you progress.
- **COMPLETE ALL**: You CANNOT use the `response` tool until ALL tasks are marked as `[x]` completed.
- **NO SKIPPING**: Every task you registered must be completed — you cannot simply remove tasks to "complete" early.

### Example workflow:
```
1. Call update_task_list with full plan:
   [ ] Scaffold Next.js project
   [ ] Design architecture
   [ ] Build API routes
   [ ] Build frontend
   [ ] Test and verify

2. Start working, update as you go:
   [x] Scaffold Next.js project
   [-] Design architecture
   [ ] Build API routes
   [ ] Build frontend
   [ ] Test and verify

3. Only after ALL tasks are [x]:
   [x] Scaffold Next.js project
   [x] Design architecture
   [x] Build API routes
   [x] Build frontend
   [x] Test and verify
   → NOW you can call `response` tool
```
