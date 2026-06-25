from __future__ import annotations
from typing import Any

try:  # Optional dependency: browser_use is only present in some runtimes
    from browser_use.llm import ChatGoogle  # type: ignore[import]
except Exception:  # pragma: no cover - exercised indirectly in tests
    ChatGoogle = None  # type: ignore[assignment]

from python.helpers import dirty_json


# ------------------------------------------------------------------------------
# Gemini Helper for Output Conformance
# ------------------------------------------------------------------------------
# This function sanitizes and conforms the JSON output from Gemini to match
# the specific schema expectations of the browser-use library. It handles
# markdown fences, aliases actions (like 'complete_task' to 'done'), and
# intelligently constructs a valid 'data' object for the final action.

def clean_and_conform_browser_use_output(text: str):
    obj = None
    try:
        # dirty_json parser is robust enough to handle markdown fences and minor syntax errors
        obj = dirty_json.parse(text)
    except Exception:
        return text  # Return original text if parsing fails completely

    if not isinstance(obj, dict):
        return text

    # Conform actions to browser-use expectations
    if isinstance(obj.get("action"), list):
        normalized_actions = []
        for item in obj["action"]:
            if not isinstance(item, dict):
                continue  # Skip non-dict items

            action_key, action_value = next(iter(item.items()), (None, None))
            if not action_key:
                continue

            # Alias 'complete_task' to 'done' (used in our browser_agent.py registry)
            if action_key == "complete_task":
                action_key = "done"

            # Create a mutable copy of the value
            v = (action_value or {}).copy()

            # 1. Fix 'wait' action common errors
            if action_key == "wait":
                # LLMs often use 'timeout' instead of 'seconds'
                if "timeout" in v and "seconds" not in v:
                    v["seconds"] = v.pop("timeout")
                # Ensure it's a dict item
                normalized_actions.append({"wait": v})

            # 2. Fix 'index' fields - LLMs sometimes return strings instead of ints
            elif any(k == "index" for k in v.keys()):
                try:
                    if isinstance(v.get("index"), str):
                        v["index"] = int(v["index"])
                except (ValueError, TypeError):
                    pass
                normalized_actions.append({action_key: v})

            # 3. Handle scroll actions
            elif action_key in ("scroll_down", "scroll_up", "scroll"):
                is_down = action_key != "scroll_up"
                v.setdefault("down", is_down)
                v.setdefault("num_pages", 1.0)
                normalized_actions.append({"scroll": v})

            # 4. Handle navigation
            elif action_key == "go_to_url":
                v.setdefault("new_tab", False)
                normalized_actions.append({action_key: v})

            # 5. Handle completion action ('done' in our registry)
            elif action_key == "done":
                # If these fields are at the top level of 'done', but our schema expects 'DoneResult' fields:
                # title, response, page_summary are the fields in our browser_agent.py's DoneResult
                
                # If `data` is present (browser-use structured output style), leave it?
                # Actually, our registry uses `DoneResult` directly as `param_model`.
                
                # Ensure success is present
                if "success" not in v:
                    v["success"] = True
                
                # If the LLM returned it in a 'data' sub-field (common mistake), flatten it
                if "data" in v and isinstance(v["data"], dict):
                    data = v.pop("data")
                    for k, val in data.items():
                        v.setdefault(k, val)
                
                normalized_actions.append({action_key: v})
            else:
                normalized_actions.append({action_key: v})
        
        obj["action"] = normalized_actions

    # Handle completion outside of 'action' list (if LLM took a shortcut)
    if "done" in obj and "action" not in obj:
        obj["action"] = [{"done": obj["done"]}]

    # Ensure other required AgentOutput fields are present
    obj.setdefault("thinking", "")
    obj.setdefault("evaluation_previous_goal", "")
    obj.setdefault("memory", "")
    obj.setdefault("next_goal", "")

    return dirty_json.stringify(obj)

# Keep alias for backward compatibility in models.py
gemini_clean_and_conform = clean_and_conform_browser_use_output

# ------------------------------------------------------------------------------
# Monkey-patch for browser-use Gemini schema issue
# ------------------------------------------------------------------------------
# The original _fix_gemini_schema in browser_use.llm.google.chat.ChatGoogle
# removes the 'title' property but fails to remove it from the 'required' list,
# causing a validation error with the Gemini API. This patch corrects that behavior.

def _patched_fix_gemini_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a Pydantic model to a Gemini-compatible schema.

    This function removes unsupported properties like 'additionalProperties' and resolves
    $ref references that Gemini doesn't support.
    """

    # Handle $defs and $ref resolution
    if '$defs' in schema:
        defs = schema.pop('$defs')

        def resolve_refs(obj: Any) -> Any:
            if isinstance(obj, dict):
                if '$ref' in obj:
                    ref = obj.pop('$ref')
                    ref_name = ref.split('/')[-1]
                    if ref_name in defs:
                        # Replace the reference with the actual definition
                        resolved = defs[ref_name].copy()
                        # Merge any additional properties from the reference
                        for key, value in obj.items():
                            if key != '$ref':
                                resolved[key] = value
                        return resolve_refs(resolved)
                    return obj
                else:
                    # Recursively process all dictionary values
                    return {k: resolve_refs(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [resolve_refs(item) for item in obj]
            return obj

        schema = resolve_refs(schema)

    # Remove unsupported properties
    def clean_schema(obj: Any) -> Any:
        if isinstance(obj, dict):
            # Remove unsupported properties
            cleaned = {}
            for key, value in obj.items():
                if key not in ['additionalProperties', 'title', 'default']:
                    cleaned_value = clean_schema(value)
                    # Handle empty object properties - Gemini doesn't allow empty OBJECT types
                    if (
                        key == 'properties'
                        and isinstance(cleaned_value, dict)
                        and len(cleaned_value) == 0
                        and isinstance(obj.get('type', ''), str)
                        and obj.get('type', '').upper() == 'OBJECT'
                    ):
                        # Convert empty object to have at least one property
                        cleaned['properties'] = {'_placeholder': {'type': 'string'}}
                    else:
                        cleaned[key] = cleaned_value

            # If this is an object type with empty properties, add a placeholder
            if (
                isinstance(cleaned.get('type', ''), str)
                and cleaned.get('type', '').upper() == 'OBJECT'
                and 'properties' in cleaned
                and isinstance(cleaned['properties'], dict)
                and len(cleaned['properties']) == 0
            ):
                cleaned['properties'] = {'_placeholder': {'type': 'string'}}

            # PATCH: Also remove 'title' from the required list if it exists
            if 'required' in cleaned and isinstance(cleaned.get('required'), list):
                cleaned['required'] = [p for p in cleaned['required'] if p != 'title']

            return cleaned
        elif isinstance(obj, list):
            return [clean_schema(item) for item in obj]
        return obj

    return clean_schema(schema)

def apply():
    """Applies the monkey-patch to ChatGoogle if available.

    In environments where the optional ``browser_use`` package is not
    installed (such as the standard dev/test setup), this becomes a
    no-op so that importing [`agix/models`](agix/models.py:line)
    or [`python.helpers.browser_use_monkeypatch`](agix/python/helpers/browser_use_monkeypatch.py:line)
    does not fail.
    """

    if ChatGoogle is None:  # browser_use not installed; nothing to patch
        return

    ChatGoogle._fix_gemini_schema = _patched_fix_gemini_schema
