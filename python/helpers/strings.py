from __future__ import annotations
import re
import sys
import time
import os
from python.helpers import files

def sanitize_string(s: str, encoding: str = "utf-8") -> str:
    # Replace surrogates and invalid unicode with replacement character
    if not isinstance(s, str):
        s = str(s)
    return s.encode(encoding, 'replace').decode(encoding, 'replace')

def sanitize_surrogates(value):
    """Strip lone surrogate characters (U+D800–U+DFFF) from a string.

    LLM responses and MCP tool outputs can contain surrogate characters
    that cause UnicodeEncodeError when downstream code calls .encode('utf-8').
    This function silently removes them at the ingestion boundary.

    Unlike sanitize_string() which replaces with U+FFFD (�), this function
    strips surrogates entirely — cleaner for user-facing LLM content.

    Args:
        value: Any value. Only strings are sanitized; non-strings pass through.

    Returns:
        Sanitized string if input was str, otherwise the original value unchanged.
    """
    if not isinstance(value, str):
        return value
    if not value:
        return value
    # encode with surrogatepass to handle surrogates, then decode ignoring them
    # This round-trip strips lone surrogates while preserving all valid Unicode
    return value.encode('utf-8', 'surrogatepass').decode('utf-8', 'ignore')

def calculate_valid_match_lengths(first, second, 
                                  deviation_threshold=5, 
                                  deviation_reset=5, 
                                  ignore_patterns=None,
                                  debug=False):
    
    first_length = len(first)
    second_length = len(second)

    i, j = 0, 0
    deviations = 0
    matched_since_deviation = 0
    last_matched_i, last_matched_j = 0, 0  # Track the last matched index

    def skip_ignored_patterns(s, index):
        """Skip characters in `s` that match any pattern in `ignore_patterns` starting from `index`."""
        while index < len(s):
            for pattern in ignore_patterns:
                match = re.match(pattern, s[index:])
                if match:
                    index += len(match.group(0))
                    break
            else:
                break
        return index

    while i < first_length and j < second_length:
        # Skip ignored patterns
        i = skip_ignored_patterns(first, i)
        j = skip_ignored_patterns(second, j)

        if i < first_length and j < second_length and first[i] == second[j]:
            last_matched_i, last_matched_j = i + 1, j + 1  # Update last matched position
            i += 1
            j += 1
            matched_since_deviation += 1

            # Reset the deviation counter if we've matched enough characters since the last deviation
            if matched_since_deviation >= deviation_reset:
                deviations = 0
                matched_since_deviation = 0
        else:
            # Determine the look-ahead based on the remaining deviation threshold
            look_ahead = deviation_threshold - deviations

            # Look ahead to find the best match within the remaining deviation allowance
            best_match = None
            for k in range(1, look_ahead + 1):
                if i + k < first_length and j < second_length and first[i + k] == second[j]:
                    best_match = ('i', k)
                    break
                if j + k < second_length and i < first_length and first[i] == second[j + k]:
                    best_match = ('j', k)
                    break

            if best_match:
                if best_match[0] == 'i':
                    i += best_match[1]
                elif best_match[0] == 'j':
                    j += best_match[1]
            else:
                i += 1
                j += 1

            deviations += 1
            matched_since_deviation = 0

            if deviations > deviation_threshold:
                break

        if debug:
            output = (
                f"First (up to {last_matched_i}): {first[:last_matched_i]!r}\n"
                "\n"
                f"Second (up to {last_matched_j}): {second[:last_matched_j]!r}\n"
                "\n"
                f"Current deviation: {deviations}\n"
                f"Matched since last deviation: {matched_since_deviation}\n"
                + "-" * 40 + "\n"
            )
            sys.stdout.write("\r" + output)
            sys.stdout.flush()
            time.sleep(0.01)  # Add a short delay for readability (optional)

    # Return the last matched positions instead of the current indices
    return last_matched_i, last_matched_j

def format_key(key: str) -> str:
    """Format a key string to be more readable.
    Converts camelCase and snake_case to Title Case with spaces."""
    # First replace non-alphanumeric with spaces
    result = ''.join(' ' if not c.isalnum() else c for c in key)
    
    # Handle camelCase
    formatted = ''
    for i, c in enumerate(result):
        if i > 0 and c.isupper() and result[i-1].islower():
            formatted += ' ' + c
        else:
            formatted += c
            
    # Split on spaces and capitalize each word
    return ' '.join(word.capitalize() for word in formatted.split())

def dict_to_text(d: dict) -> str:
    parts = []
    for key, value in d.items():
        parts.append(f"{format_key(str(key))}:")
        parts.append(f"{value}")
        parts.append("")  # Add empty line between entries
    
    return "\n".join(parts).rstrip()  # rstrip to remove trailing newline

def truncate_text(text: str, length: int, at_end: bool = True, replacement: str = "...") -> str:
    orig_length = len(text)
    if orig_length <= length:
        return text
    if at_end:
         return text[:length] + replacement
    else:
        return replacement + text[-length:]
    
def truncate_text_by_ratio(text: str, threshold: int, replacement: str = "...", ratio: float = 0.5) -> str:
    """Truncate text with replacement at a specified ratio position."""
    threshold = int(threshold)
    if not threshold or len(text) <= threshold:
        return text
    
    # Clamp ratio to valid range
    ratio = max(0.0, min(1.0, float(ratio)))
    
    # Calculate available space for original text after accounting for replacement
    available_space = threshold - len(replacement)
    if available_space <= 0:
        return replacement[:threshold]
    
    # Handle edge cases for efficiency
    if ratio == 0.0:
        # Replace from start: "...text"
        return replacement + text[-available_space:]
    elif ratio == 1.0:
        # Replace from end: "text..."
        return text[:available_space] + replacement
    else:
        # Replace in middle based on ratio
        start_len = int(available_space * ratio)
        end_len = available_space - start_len
        return text[:start_len] + replacement + text[-end_len:]


def replace_file_includes(text: str, placeholder_pattern: str = r"§§include\(([^)]+)\)", wrap_in_markers: bool = False) -> str:
    """
    Replace §§include(path) placeholders with actual file content.
    
    CRITICAL: If file doesn't exist, return a clear error message instead of
    the raw placeholder macro (which would be confusing in external APIs).
    """
    if not text:
        return text

    def _repl(match):
        path = match.group(1)
        try:
            # 1. Canonical normalization (fix_dev_path handles single /agix/ stripping)
            fixed_path = files.fix_dev_path(path)
            if os.path.exists(fixed_path):
                content = files.read_file(fixed_path)
                if wrap_in_markers:
                    basename = os.path.basename(fixed_path)
                    content = f"\n<!-- INCLUDED_DOCUMENT_START: {basename} -->\n{content}\n<!-- INCLUDED_DOCUMENT_END -->\n"
                return content
            
            # 2. Robust recursive stripping (fix for nested virtual root paths)
            # Remove all leading virtual roots and slashes to get a clean relative project path
            clean_path = path
            while True:
                last_path = clean_path
                clean_path = clean_path.lstrip(os.path.sep).lstrip("./")
                if clean_path.startswith("agix/"):
                    clean_path = clean_path[3:]
                if clean_path == last_path:
                    break
            
            # Resolve to physical absolute path directly relative to base_dir
            # We bypass get_abs_path here as it might re-prepend /agix/ based on env
            physical_path = os.path.join(files.get_base_dir(), clean_path)
            
            if os.path.exists(physical_path):
                content = files.read_file(physical_path)
                if wrap_in_markers:
                    basename = os.path.basename(physical_path)
                    content = f"\n<!-- INCLUDED_DOCUMENT_START: {basename} -->\n{content}\n<!-- INCLUDED_DOCUMENT_END -->\n"
                return content
                
            raise FileNotFoundError(f"File not found: {physical_path}")
            
        except Exception as e:
            # CRITICAL FIX: Return error message instead of raw placeholder
            # This prevents §§include(...) from appearing in external API calls
            import logging
            logging.getLogger("strings").warning(f"Failed to expand §§include({path}): {e}")
            return f"[Content unavailable: {path}]"

    return re.sub(placeholder_pattern, _repl, text)

def clean_string(input_string):
    # Remove ANSI escape codes
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    cleaned = ansi_escape.sub("", input_string)

    # remove null bytes
    cleaned = cleaned.replace("\x00", "")

    # remove ipython \r\r\n> sequences from the start
    cleaned = re.sub(r'^[ \r]*(?:\r*\n>[ \r]*)*', '', cleaned)
    # also remove any amount of '> ' sequences from the start
    cleaned = re.sub(r'^(>\s*)+', '', cleaned)

    # Replace '\r\n' with '\n'
    cleaned = cleaned.replace("\r\n", "\n")

    # remove leading \r and spaces
    cleaned = cleaned.lstrip("\r ")

    # Split the string by newline characters to process each segment separately
    lines = cleaned.split("\n")

    for i in range(len(lines)):
        # Handle carriage returns '\r' by splitting and taking the last part
        parts = [part for part in lines[i].split("\r") if part.strip()]
        if parts:
            lines[i] = parts[-1].rstrip()  # Overwrite with the last part after the last '\r'

    return "\n".join(lines)