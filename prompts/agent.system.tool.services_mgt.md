# Tool: services_mgt

Use this tool to manage and verify background services (like web servers, APIs, or dev servers) hosted within the environment.

## Port Allocation
- **Port is auto-assigned** if you omit the `port` parameter. The system uses `PortManager` to allocate a unique, hash-based port per project — preventing cross-project collisions.
- You may pass `port` explicitly if needed, but **prefer omitting it** to let the system allocate dynamically.
- Valid range: **5100-5500**
- Docker-Mapped Range: **5100-5199** (accessible from the host browser at `http://localhost:<port>`)

## Actions

### check_port
Check if a port is available or currently in use by another process.
- **port**: The port number (5100-5500).

### start_service
Start a new background service. The service will be detached and continue running across agent turns.
- **command**: The shell command to start the service (e.g., `npm run dev -- --port $PORT`). Use the port returned in the response.
- **port**: (Optional) The port to use. If omitted, a port is **auto-allocated** via PortManager based on the current project name.
- **name**: (Optional) A friendly name for the service.

### stop_service
Stop a service that was started using this tool.
- **service_id**: The unique ID returned when the service was started.

### restart_service
Restart a managed service — stops the existing process, **clears the .next/build cache**, and re-launches on the same port with the same command. This is critical after modifying source files post-startup: stale webpack/HMR caches cause MODULE_NOT_FOUND errors and 404s on all routes.
- **service_id**: (Optional) The unique ID returned from start_service. Provide either service_id OR port.
- **port**: (Optional) The port of the service to restart. Provide either service_id OR port.

**When to use?** Always restart after bulk file modifications (3+ files changed since server start). The system will inject a warning if it detects this condition.

### list_services
List all services currently managed by this tool and their status (running/stopped).

### test_service
Verify if a service is responding using an internal `curl` request.
- **port**: The port to test.

### get_service_logs
Read the dev server's stderr output to diagnose errors. Returns logs using a **middle-out** strategy by default (first N + last N lines, omitting webpack noise in the middle), or grep for error patterns across the entire log.
- **port**: The port of the service whose logs to read.
- **service_id**: (Optional) Alternative to port for service lookup.
- **lines**: (Optional, default 30) Number of lines to show from head and tail.
- **filter**: (Optional) Set to `"errors"` to grep the entire log for error patterns (Error, Exception, FATAL, 500, ENOENT, etc.) and return matching lines with ±3 lines of context.

**Output modes:**
- **Default (middle-out):** Shows first 30 lines (startup) + last 30 lines (recent), omitting the middle. Captures both startup errors (e.g. PrismaClientInitializationError) and recent request errors (e.g. GET /api/x 500).
- **Errors filter:** Scans the entire log for error patterns and returns matched lines with surrounding context. Use this when the default mode doesn't show the error.

## Best Practices
1. **Omit `port`** to let the system auto-allocate via PortManager — this prevents cross-project port collisions.
2. **Always check the port** before starting a service to avoid conflicts.
3. **Bind to 0.0.0.0** if you want the service to be accessible outside the container.
4. **Use `npm run dev`** (not `npm start`) during development — it auto-reloads on file changes and avoids stale build issues.
5. **Use `restart_service`** (not stop + start) after modifying multiple source files — it automatically clears the `.next` build cache to prevent stale chunk errors.
6. **Cleanup**: Stop services when they are no longer needed.
7. **🔴 When API routes return 500**: FIRST use `get_service_logs` (with `filter: "errors"`) to read the ACTUAL error from stderr BEFORE attempting code fixes. The 500 HTML page rarely contains useful info — the real error is in the server logs.
