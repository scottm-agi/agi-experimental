Terminal session {{session}} is still running a command. **DO NOT re-execute the same command.**

To check on progress, use `code_execution_tool` with `runtime=output` and `session={{session}}`. This will return the latest output without interrupting the running process.

If you need to abort, use `runtime=reset` with `session={{session}}` to kill the process and start fresh. Only do this if the command is clearly stuck or wrong.

**DO NOT** fire the same command again — this will create a duplicate process that conflicts with the existing one.