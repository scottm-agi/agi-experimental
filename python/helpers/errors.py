from __future__ import annotations
import re
import traceback
import asyncio


def handle_error(e: Exception):
    # if asyncio.CancelledError, re-raise
    if isinstance(e, asyncio.CancelledError):
        raise e


def error_text(e: Exception):
    return str(e)


def get_short_error(e: Exception) -> str:
    """Returns a concise one-line error description, avoiding full tracebacks."""
    error_msg = str(e)
    if not error_msg:
        return type(e).__name__
    
    # If the error message itself contains a traceback (sometimes happens with nested exceptions), 
    # try to extract just the last line.
    if "Traceback" in error_msg:
        lines = error_msg.strip().split("\n")
        # Usually the last non-empty line is the actual error
        for line in reversed(lines):
            if line.strip():
                return line.strip()
    
    return error_msg


def format_error(e: Exception, start_entries=6, end_entries=4):
    # format traceback from the provided exception instead of the most recent one
    traceback_text = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
    # Split the traceback into lines
    lines = traceback_text.split("\n")

    if not start_entries and not end_entries:
        trimmed_lines = []
    else:

        # Find all "File" lines
        file_indices = [
            i for i, line in enumerate(lines) if line.strip().startswith("File ")
        ]

        # If we found at least one "File" line, trim the middle if there are more than start_entries+end_entries lines
        if len(file_indices) > start_entries + end_entries:
            start_index = max(0, len(file_indices) - start_entries - end_entries)
            trimmed_lines = (
                lines[: file_indices[start_index]]
                + [
                    f"\n>>>  {len(file_indices) - start_entries - end_entries} stack lines skipped <<<\n"
                ]
                + lines[file_indices[start_index + end_entries] :]
            )
        else:
            # If no "File" lines found, or not enough to trim, just return the original traceback
            trimmed_lines = lines

    # Find the error message at the end
    error_message = ""
    for line in reversed(lines):
        # match both simple errors and module.path.Error patterns
        if re.match(r"[\w\.]+Error:\s*", line):
            error_message = line
            break

    # Combine the trimmed traceback with the error message
    if not trimmed_lines:
        result = error_message
    else:
        result = "Traceback (most recent call last):\n" + "\n".join(trimmed_lines)
        if error_message:
            result += f"\n\n{error_message}"

    # at least something
    if not result:
        result = str(e)

    return result


class RepairableException(Exception):
    """An exception type indicating errors that can be surfaced to the LLM for potential self-repair."""
    pass


class MissingSecretException(RepairableException):
    """An exception type indicating that a required secret (token, API key, etc.) is missing."""
    pass


class InterventionException(Exception):
    """Exception raised to interrupt the agent's current task (e.g., by a supervisor)."""
    pass


class TruncationException(Exception):
    """Exception raised when an LLM response is truncated due to length."""
    def __init__(self, partial_response: str, partial_reasoning: str, model: str, provider: str):
        self.partial_response = partial_response
        self.partial_reasoning = partial_reasoning
        self.model = model
        self.provider = provider
        super().__init__(f"LLM response truncated for {provider}/{model}")


class RepetitionException(Exception):
    """Exception raised when an LLM response is detected as repetitive/looping."""
    def __init__(self, partial_response: str, partial_reasoning: str, model: str, provider: str):
        self.partial_response = partial_response
        self.partial_reasoning = partial_reasoning
        self.model = model
        self.provider = provider
        super().__init__(f"Model repetition detected for {provider}/{model}")
