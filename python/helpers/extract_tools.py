from __future__ import annotations
import re, os, importlib, importlib.util, inspect, sys
from types import ModuleType
from typing import Any, Type, TypeVar, Optional
from python.helpers.dirty_json import DirtyJson
from python.helpers.files import get_abs_path, deabsolute_path
import regex
from fnmatch import fnmatch

def _strip_dict_keys(obj: Any) -> Any:
    """Recursively strip whitespace from all dict keys.

    LLMs sometimes output JSON with trailing/leading spaces in keys like
    ``"tool_name ": "call_subordinate"`` which breaks ``dict.get("tool_name")``.
    """
    if isinstance(obj, dict):
        return {k.strip() if isinstance(k, str) else k: _strip_dict_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_dict_keys(item) for item in obj]
    return obj


def json_parse_dirty(json:str) -> Optional[dict[str,Any]]:
    if not json or not isinstance(json, str):
        return None

    ext_json = extract_json_object_string(json.strip())
    if ext_json:
        try:
            data = DirtyJson.parse_string(ext_json)
            if isinstance(data, dict):
                return _strip_dict_keys(data)
        except Exception:
            # If parsing fails, return None instead of crashing
            return None
    return None


def extract_all_json_objects(content: Any) -> list[str]:
    """Extract ALL top-level JSON objects from a string using brace-depth tracking.

    Unlike extract_json_object_string() which uses rfind('}') and collapses
    multiple JSON objects into one malformed blob, this function correctly
    separates each top-level JSON object by tracking brace depth and respecting
    string boundaries.

    Args:
        content: Raw LLM output string (or None)

    Returns:
        List of JSON object strings, one per top-level {...} block found.
        Empty list if no JSON objects are found.
    """
    if not content or not isinstance(content, str):
        return []

    content = content.strip()
    objects: list[str] = []
    pos = 0

    while pos < len(content):
        # Find the next '{' that starts a top-level JSON object
        start = content.find('{', pos)
        if start == -1:
            break

        # Track brace depth to find the matching '}'
        depth = 0
        in_string = False
        escape_next = False
        end = -1

        for i in range(start, len(content)):
            c = content[i]

            if escape_next:
                escape_next = False
                continue

            if c == '\\' and in_string:
                escape_next = True
                continue

            if c == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end == -1:
            # Incomplete JSON object — still capture it for DirtyJson to repair
            objects.append(content[start:])
            break

        objects.append(content[start:end + 1])
        pos = end + 1

    return objects


def json_parse_dirty_all(json_str: Any) -> list[dict[str, Any]]:
    """Parse ALL tool call JSON objects from LLM output into a list of dicts.

    This is the multi-tool equivalent of json_parse_dirty(). It extracts every
    top-level JSON object from the message, parses each with DirtyJson (which
    handles malformed JSON), and returns all successfully-parsed dicts.

    Args:
        json_str: Raw LLM output string (or None)

    Returns:
        List of parsed dicts. Empty list if nothing parseable.
        Malformed objects are skipped (not fatal).
    """
    if not json_str or not isinstance(json_str, str):
        return []

    raw_objects = extract_all_json_objects(json_str.strip())
    parsed: list[dict[str, Any]] = []

    for raw in raw_objects:
        try:
            data = DirtyJson.parse_string(raw)
            if isinstance(data, dict):
                parsed.append(_strip_dict_keys(data))
        except Exception:
            # Skip unparseable objects — don't crash the whole batch
            continue

    return parsed


def detect_multi_tool_batching(msg: str) -> int:
    """Detect if the LLM output contains multiple concatenated JSON tool calls.

    The framework processes exactly ONE tool call per LLM turn. If the LLM
    batches N tool calls, N-1 are silently dropped (RCA-304). This function
    detects the batching so the framework can warn the agent.

    Algorithm:
    1. Find the first complete top-level JSON object using brace-depth tracking
    2. After that object ends, scan the REMAINDER for additional "tool_name" keys
       that appear at the start of a new JSON block (not inside string content)
    3. Return the count of additional (dropped) tool calls

    Args:
        msg: Raw LLM output string

    Returns:
        Number of ADDITIONAL tool calls beyond the first (0 = no batching)
    """
    if not msg or not isinstance(msg, str):
        return 0

    msg = msg.strip()

    # Step 1: Find the end of the first top-level JSON object using brace depth
    first_brace = msg.find('{')
    if first_brace == -1:
        return 0

    depth = 0
    in_string = False
    escape_next = False
    first_obj_end = -1

    for i in range(first_brace, len(msg)):
        c = msg[i]

        if escape_next:
            escape_next = False
            continue

        if c == '\\' and in_string:
            escape_next = True
            continue

        if c == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                first_obj_end = i
                break

    if first_obj_end == -1:
        return 0  # No complete JSON object found

    # Step 2: Check the remainder after the first JSON object
    remainder = msg[first_obj_end + 1:]

    if not remainder.strip():
        return 0  # Nothing after the first object

    # Step 3: Count additional top-level JSON blocks that contain "tool_name"
    # We look for patterns like: { ... "tool_name": "..." ... }
    # These are NEW JSON objects, not content inside the first one
    additional_count = 0

    # Find all top-level { in the remainder
    pos = 0
    while pos < len(remainder):
        next_brace = remainder.find('{', pos)
        if next_brace == -1:
            break

        # Check if this is a top-level JSON object (preceded only by whitespace)
        before = remainder[pos:next_brace].strip()
        if before and not before.startswith('}'):
            # There's non-whitespace/non-closing-brace before this {
            # Could be text, skip to next
            pos = next_brace + 1
            continue

        # Extract this JSON block using brace depth
        block_depth = 0
        block_in_string = False
        block_escape = False
        block_end = -1

        for j in range(next_brace, len(remainder)):
            ch = remainder[j]

            if block_escape:
                block_escape = False
                continue
            if ch == '\\' and block_in_string:
                block_escape = True
                continue
            if ch == '"' and not block_escape:
                block_in_string = not block_in_string
                continue
            if block_in_string:
                continue
            if ch == '{':
                block_depth += 1
            elif ch == '}':
                block_depth -= 1
                if block_depth == 0:
                    block_end = j
                    break

        if block_end == -1:
            break  # Incomplete block

        block = remainder[next_brace:block_end + 1]

        # Check if this block contains a top-level "tool_name" key
        # We need to verify it's NOT inside a nested string value
        # Simple heuristic: check if "tool_name" appears as a key (with : after)
        # at the top level of this block
        if _block_has_tool_name_key(block):
            additional_count += 1

        pos = block_end + 1

    return additional_count


def _block_has_tool_name_key(block: str) -> bool:
    """Check if a JSON block has 'tool_name' as a top-level key (not inside a string value).

    Uses depth tracking to only match "tool_name" at depth 1 (top-level keys).
    """
    depth = 0
    in_string = False
    escape_next = False
    i = 0

    while i < len(block):
        c = block[i]

        if escape_next:
            escape_next = False
            i += 1
            continue

        if c == '\\' and in_string:
            escape_next = True
            i += 1
            continue

        if c == '"' and not escape_next:
            if not in_string:
                in_string = True
                # Check if this string at depth 1 is "tool_name"
                if depth == 1:
                    candidate = block[i:i+12]  # len('"tool_name"') = 11
                    if candidate.startswith('"tool_name"'):
                        # Verify it's followed by : (a key, not a value)
                        after = block[i+11:].lstrip()
                        if after and after[0] == ':':
                            return True
                i += 1
                continue
            else:
                in_string = False
                i += 1
                continue

        if in_string:
            i += 1
            continue

        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1

        i += 1

    return False



def extract_json_object_string(content):
    start = content.find('{')
    if start == -1:
        return ""

    # Find the first '{'
    end = content.rfind('}')
    if end == -1:
        # If there's no closing '}', return from start to the end
        return content[start:]
    else:
        # If there's a closing '}', return the substring from start to end
        return content[start:end+1]

def extract_json_string(content):
    # Regular expression pattern to match a JSON object
    pattern = r'\{(?:[^{}]|(?R))*\}|\[(?:[^\[\]]|(?R))*\]|"(?:\\.|[^"\\])*"|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?'

    # Search for the pattern in the content
    match = regex.search(pattern, content)

    if match:
        # Return the matched JSON string
        return match.group(0)
    else:
        return ""

def fix_json_string(json_string):
    # Function to replace unescaped line breaks within JSON string values
    def replace_unescaped_newlines(match):
        return match.group(0).replace('\n', '\\n')

    # Use regex to find string values and apply the replacement function
    fixed_string = re.sub(r'(?<=: ")(.*?)(?=")', replace_unescaped_newlines, json_string, flags=re.DOTALL)
    return fixed_string


T = TypeVar('T')  # Define a generic type variable

def import_module(file_path: str) -> ModuleType:
    # Handle file paths with periods in the name using importlib.util
    abs_path = get_abs_path(file_path)
    module_name = os.path.basename(abs_path).replace('.py', '')
    
    # Create the module spec and load the module
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {abs_path}")
        
    module = importlib.util.module_from_spec(spec)
    # Register the module BEFORE executing it to handle circular imports and tool resolution
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

def load_classes_from_folder(folder: str, name_pattern: str, base_class: Type[T], one_per_file: bool = True) -> list[Type[T]]:
    classes = []
    abs_folder = get_abs_path(folder)

    # Get all .py files in the folder that match the pattern, sorted alphabetically
    py_files = sorted(
        [file_name for file_name in os.listdir(abs_folder) if fnmatch(file_name, name_pattern) and file_name.endswith(".py")]
    )

    # Iterate through the sorted list of files
    for file_name in py_files:
        file_path = os.path.join(abs_folder, file_name)
        # Use the new import_module function
        module = import_module(file_path)

        # Get all classes in the module
        class_list = inspect.getmembers(module, inspect.isclass)

        # Filter for classes that are subclasses of the given base_class
        # and are defined IN this module (not imported)
        for cls in reversed(class_list):
            if (
                cls[1] is not base_class 
                and issubclass(cls[1], base_class)
                and cls[1].__module__ == module.__name__
            ):
                classes.append(cls[1])
                if one_per_file:
                    break

    return classes

def load_classes_from_file(file: str, base_class: type[T], one_per_file: bool = True) -> list[type[T]]:
    classes = []
    # Use the new import_module function
    module = import_module(file)
    
    # Get all classes in the module
    class_list = inspect.getmembers(module, inspect.isclass)
    
    # Filter for classes that are subclasses of the given base_class
    # iterate backwards to skip imported superclasses
    for cls in reversed(class_list):
        if cls[1] is not base_class and issubclass(cls[1], base_class):
            classes.append(cls[1])
            if one_per_file:
                break
                
    return classes
