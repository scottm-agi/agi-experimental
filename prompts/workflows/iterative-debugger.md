---
description: Iterative Docker debugger workflow — automatically fetch logs, extract errors, analyze, fix, and verify
---

# Docker Error Fixer Workflow

This workflow automates checking Docker logs for errors, analyzing them, and implementing fixes.

## Prerequisites
- Docker Compose running the target service
- Access to the codebase for implementing fixes

## Steps

### 1. Fetch Docker Logs
// turbo
```bash
docker compose -f docker/run/docker-compose.yml logs --tail=1000 > /tmp/docker-errors.log
```

### 2. Extract Unique Errors
Scan the log file for unique error patterns:
// turbo
```bash
grep -i "error\|exception\|traceback\|failed\|critical" /tmp/docker-errors.log | sort -u > /tmp/unique-errors.log
```

### 3. Analyze Each Error
For each unique error in `/tmp/unique-errors.log`:
1. **Identify Root Cause**: Search the codebase for the relevant code section
2. **Classify Severity**: Critical (crash), High (functionality broken), Medium (degraded), Low (warning)
3. **Determine Fix Location**: Which file(s) need modification

### 4. Implement Fix
For each identified error:
1. **Read the full file** before making changes
2. **Write a test first** (TDD) that reproduces the error condition
3. **Apply the fix** using code edit tools
4. **Verify syntax**: `python3 -m py_compile <modified_file>`

### 5. Restart and Verify
// turbo
```bash
docker compose -f docker/run/docker-compose.yml restart
```
Wait 10 seconds, then check logs:
// turbo
```bash
docker compose -f docker/run/docker-compose.yml logs --tail=100 2>&1 | grep -i "error\|exception"
```

### 6. Iterate
If new errors appear after fixing:
1. Return to Step 1
2. Repeat until no critical/high errors remain
3. Maximum 5 iterations to prevent infinite loops

### 7. Document Results
1. Create/update Forgejo issues for significant bugs found
2. Update memory bank with lessons learned
3. Commit all fixes with descriptive messages

## Important Notes
- **Never fix errors blindly** — always understand the root cause first
- **TDD is mandatory** — write the test before the fix
- **One fix at a time** — restart and verify after each fix to isolate regressions
- **Preserve existing behavior** — don't break working features while fixing bugs
