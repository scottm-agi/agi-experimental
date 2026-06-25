Execution reached the {{timeout}}-second hard cap. The process has been terminated.

If this was a long-running command (npm install, pip install, build), consider:
1. Check if the command is actually still needed (it may have completed before timeout)
2. If you need to retry, use `runtime=terminal` with the same session to re-run
3. For future long-running commands, use `runtime=output` with the same session to periodically check progress instead of waiting for full completion

**DO NOT** blindly re-run the same command — verify the current state first.