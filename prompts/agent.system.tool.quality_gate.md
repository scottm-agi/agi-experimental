## quality_gate

Control the orchestrator quality gate for integration checks (build verification, browser UAT, API validation).

**When to use:**
- User asks to "Run UAT", "Run quality gate", or "verify the build"
- Before delivering a web project that needs integration testing
- To check if quality gate is currently active

**Actions:**
- `enable` — Turn on integration checks for this session
- `disable` — Turn off integration checks
- `status` — Show current gate state and project info
- `assess` — Analyze current project to determine if quality gate is needed

**Example usage:**
~~~json
{
    "action": "enable"
}
~~~

~~~json
{
    "action": "assess"
}
~~~

**Important:** Quality gate is OFF by default for non-multiagentdev agents. Only enable it when the user explicitly requests UAT or quality verification, or when you assess that the current project is a web project requiring build checks.
