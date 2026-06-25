No new output for {{timeout}} seconds. The process may still be running in the background.

To check if it's still active, use `runtime=output` with the same session number. This will return any new output without interrupting the process.

If the process is genuinely stuck (no output after multiple checks), use `runtime=reset` to kill it and start fresh.

**DO NOT** re-execute the same command — this creates duplicate processes that conflict with each other.