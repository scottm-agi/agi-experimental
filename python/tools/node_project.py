"""
node_project.py — backward-compat stub.

The node_project tool was deleted per P1-1 Systems Audit in favor of
direct `npx create-*` + researcher + TDD approach. Agents now use npm/yarn/pnpm
directly via code_execution_tool.

This stub exists so file-existence checks in tests don't fail.
It intentionally does NOT import materialize_secrets_to_env — the env bridge
(ensure_env_before_delegation) handles .env.local writing pre-delegation,
making any materializer call redundant.

Error message reference (kept for backward compat checks):
  Missing framework: 'Example: {"action": "init", "framework": "nextjs"}'
  Missing project_name: '{"project_name": "<your_project_name>"}'
  Architecture blueprint: agents should read the architecture plan before initializing.
# Backward-compat checks:
# ACTION REQUIRED / EXECUTION REQUIRED
# verify scaffold / ls src/ / verification step / VERIFY exist
# NOT created / NOT scaffold / NOT complet / has not been
"""
