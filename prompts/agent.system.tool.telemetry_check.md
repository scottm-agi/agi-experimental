# telemetry_check

Scans the specified path for known telemetry and tracking patterns (Segment, Google Analytics, Sentry, Mixpanel, etc.) to ensure privacy compliance and zero-leakage.

## Parameters
- `path` (string, optional): The absolute path to scan. Defaults to the current directory (`.`).
- `recursive` (boolean, optional): Whether to scan subdirectories. Defaults to `True`.

## Usage
Use this tool when you need to verify that a project or specific files do not contain external tracking scripts or telemetry endpoints. It is particularly useful during pre-deployment audits or when integrating third-party code.

```json
{
  "path": "/agix/usr/projects/my-project",
  "recursive": true
}
```

## Success Indicators
- Returns a `status: "success"` with `"message": "No telemetry patterns detected."` if the scan is clean.
- Returns a `status: "warning"` with a list of `findings` if patterns are located. Each finding includes the service type, the matching line number, and the source file path.
