"""
Tool Schema Validator — Pre-execution argument validation for tool calls.

Validates tool_args against declared JSON-like schemas BEFORE tool execution.
Supports:
- Static required fields (always required)
- Conditional required fields (required based on runtime context)
- Type checking (string, array, etc.)
- Backward compatible (tools without schemas pass through)
- Formatted error messages with schema hints

Architecture:
    LLM output → DirtyJson parse → tool_args dict
        → ToolSchemaValidator.validate(tool_name, tool_args, agent_data)
        → ValidationResult(valid=True/False, errors=[], error_message="")
        → if invalid: return error_message to LLM (tool not executed)

Schemas are registered in TOOL_SCHEMAS dict. Only tools with schemas
are validated — all others pass through for backward compatibility.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.tool_schema_validator")

# ── Profiles that execute work (require requirement_ids when reqs exist) ──
_WORK_EXECUTING_PROFILES = {"code", "debug", "e2e", "review", "frontend"}


@dataclass
class ValidationResult:
    """Result of tool argument validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    error_message: str = ""


# ── Schema Definitions ──
# JSON Schema-like format with 'properties', 'required', and 'conditional_required'.
# 'conditional_required' entries are evaluated at runtime via _check_condition().

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "call_subordinate": {
        "properties": {
            "message": {
                "type": "string",
                "description": "Task description for the subordinate agent"
            },
            "profile": {
                "type": "string",
                "description": "Agent profile: code, architect, debug, researcher, e2e, review, frontend"
            },
            "requirement_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "REQ-* GUIDs this delegation covers. Required for work-executing profiles when requirements exist."
            },
            "reset": {
                "type": "string",
                "description": "If 'true', create a new subordinate agent instead of reusing existing"
            },
            "relay_response": {
                "type": "string",
                "description": "If 'true', emit subordinate's result as this agent's response"
            },
        },
        "required": ["message", "profile"],
        "conditional_required": [
            {
                "field": "requirement_ids",
                "condition": "requirements_exist_for_work_profile",
                "message": (
                    "Required when tracked requirements exist and profile is a "
                    "work-executing profile (code, debug, e2e, review, frontend). "
                    "Check requirements_ledger.json for available REQ-* GUIDs."
                ),
                "non_empty": True,  # empty list also fails
            }
        ]
    },
    "call_subordinate_batch": {
        "properties": {
            "tasks": {
                "type": "array",
                "description": "List of task objects, each with 'message', 'profile', and optionally 'requirement_ids'"
            },
        },
        "required": ["tasks"],
        "conditional_required": []
    },
}


class ToolSchemaValidator:
    """Validates tool arguments against declared schemas."""

    @staticmethod
    def get_schema(tool_name: str) -> Optional[Dict[str, Any]]:
        """Get the schema for a tool, or None if not registered."""
        return TOOL_SCHEMAS.get(tool_name)

    @staticmethod
    def validate(
        tool_name: str,
        tool_args: Any,
        agent_data: Optional[Dict[str, Any]] = None,
    ) -> ValidationResult:
        """Validate tool_args against the declared schema.
        
        Args:
            tool_name: Name of the tool being called
            tool_args: The parsed arguments dict from the LLM
            agent_data: Agent runtime data for evaluating conditional requirements
            
        Returns:
            ValidationResult with valid=True if args are acceptable,
            or valid=False with errors list and formatted error_message.
        """
        schema = TOOL_SCHEMAS.get(tool_name)
        if schema is None:
            # No schema registered — pass through (backward compatible)
            return ValidationResult(valid=True)

        if agent_data is None:
            agent_data = {}

        errors: List[str] = []

        # Handle non-dict tool_args
        if tool_args is None:
            tool_args = {}
        if not isinstance(tool_args, dict):
            errors.append(
                f"tool_args must be a JSON object (dict), got {type(tool_args).__name__}. "
                f"Use format: {{\"message\": \"...\", \"profile\": \"...\"}}"
            )
            return ValidationResult(
                valid=False,
                errors=errors,
                error_message=_format_error_message(tool_name, schema, errors, tool_args)
            )

        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])

        # ── Check statically required fields ──
        for field_name in required_fields:
            if field_name not in tool_args or not tool_args[field_name]:
                field_desc = properties.get(field_name, {}).get("description", "")
                errors.append(
                    f"Missing required field '{field_name}'"
                    + (f" — {field_desc}" if field_desc else "")
                )

        # ── Check conditionally required fields ──
        for cond in schema.get("conditional_required", []):
            field_name = cond["field"]
            condition_name = cond["condition"]
            non_empty = cond.get("non_empty", False)

            if _check_condition(condition_name, tool_args, agent_data):
                value = tool_args.get(field_name)
                if value is None or (non_empty and isinstance(value, list) and len(value) == 0):
                    errors.append(
                        f"Missing conditionally required field '{field_name}' — "
                        + cond.get("message", f"Required by condition: {condition_name}")
                    )

        # ── Check types for provided fields ──
        for field_name, value in tool_args.items():
            if field_name in properties and value is not None:
                expected_type = properties[field_name].get("type")
                if expected_type and not _check_type(value, expected_type):
                    errors.append(
                        f"Field '{field_name}' has wrong type: expected {expected_type}, "
                        f"got {type(value).__name__}. "
                        + (f"Should be a {expected_type}." if expected_type == "array" else "")
                    )

        if errors:
            return ValidationResult(
                valid=False,
                errors=errors,
                error_message=_format_error_message(tool_name, schema, errors, tool_args)
            )

        return ValidationResult(valid=True)


# ── Condition Evaluators ──

def _check_condition(
    condition_name: str,
    tool_args: Dict[str, Any],
    agent_data: Dict[str, Any],
) -> bool:
    """Evaluate a named condition against runtime context.
    
    Returns True if the condition is met (meaning the field IS required).
    """
    if condition_name == "requirements_exist_for_work_profile":
        # requirement_ids is required when:
        # 1. The requirements ledger has entries, AND
        # 2. The profile is a work-executing profile
        profile = tool_args.get("profile", "")
        if profile not in _WORK_EXECUTING_PROFILES:
            return False

        ledger = agent_data.get("_requirements_ledger")
        if not ledger or not isinstance(ledger, dict):
            return False
        reqs = ledger.get("requirements", [])
        return isinstance(reqs, list) and len(reqs) > 0

    logger.warning(f"[SCHEMA] Unknown condition: {condition_name}")
    return False


def _check_type(value: Any, expected_type: str) -> bool:
    """Check if a value matches the expected JSON Schema type."""
    type_map = {
        "string": str,
        "array": list,
        "object": dict,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }
    expected_python_type = type_map.get(expected_type)
    if expected_python_type is None:
        return True  # Unknown type — allow
    return isinstance(value, expected_python_type)


def _format_error_message(
    tool_name: str,
    schema: Dict[str, Any],
    errors: List[str],
    tool_args: Any,
) -> str:
    """Format a human-readable error message with schema hint.
    
    Designed to give the LLM clear, actionable instructions to fix the call.
    """
    lines = [
        f"⛔ [SCHEMA VALIDATION] Tool '{tool_name}' called with invalid arguments.",
        "",
        "### Errors",
    ]
    for i, err in enumerate(errors, 1):
        lines.append(f"  {i}. {err}")

    lines.append("")
    lines.append("### Correct Schema")

    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))
    conditional_fields = {c["field"] for c in schema.get("conditional_required", [])}

    for param_name, param_def in properties.items():
        param_type = param_def.get("type", "any")
        description = param_def.get("description", "")
        if param_name in required_fields:
            marker = "(REQUIRED)"
        elif param_name in conditional_fields:
            marker = "(conditionally required)"
        else:
            marker = "(optional)"
        lines.append(f"  - {param_name}: {param_type} {marker} — {description}")

    lines.append("")
    lines.append("### Example")
    lines.append('```json')
    lines.append('{')
    lines.append(f'    "tool_name": "{tool_name}",')
    lines.append('    "tool_args": {')
    lines.append('        "message": "Implement the feature as designed",')
    lines.append('        "profile": "code",')
    lines.append('        "requirement_ids": ["REQ-a1b2c3d4", "REQ-e5f6a7b8"],')
    lines.append('        "reset": "true"')
    lines.append('    }')
    lines.append('}')
    lines.append('```')
    lines.append("")
    lines.append("Re-call the tool with the correct arguments.")

    return "\n".join(lines)
