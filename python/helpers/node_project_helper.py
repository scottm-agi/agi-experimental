"""
node_project_helper — backward-compat stub.

The node_project tool was deleted per P1-1 Systems Audit in favor of direct
`npx create-*` + researcher + TDD approach.

This module exists so imports don't fail.  It intentionally does NOT export
`generate_env_from_secrets` — that was a dead re-export with 0 callers that
was removed as part of System 1 secret consolidation (ADR-82).
"""
