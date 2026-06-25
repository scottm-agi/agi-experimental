from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from fnmatch import fnmatch
import json
import logging
from ntpath import isabs
import os
import sys
import re
import base64
import shutil
import tempfile
import time
from typing import Any
import zipfile
import importlib
import importlib.util
import inspect
import glob
import mimetypes

logger = logging.getLogger(__name__)


# ─── File Lock Coordination (F-7) ──────────────────────────────────
# Non-blocking .lock sidecar file approach for cross-agent write
# coordination. Features:
#   - Exponential backoff (100ms → 200ms → 400ms → ..., max 5s)
#   - Stale lock detection (dead PID or age > 30s)
#   - Never blocks forever — falls through with warning after timeout
#   - Metadata in lock file for debugging visibility


@dataclass
class FileLockResult:
    """Result of a file lock acquisition attempt."""
    acquired: bool
    lock_path: str = ""
    timed_out: bool = False


def acquire_file_lock(
    file_path: str,
    agent_name: str = "unknown",
    max_retries: int = 8,
    initial_backoff_ms: int = 100,
    max_backoff_ms: int = 5000,
    stale_timeout_s: float = 30.0,
) -> FileLockResult:
    """Acquire a .lock sidecar file for cross-agent write coordination.

    Uses atomic O_CREAT | O_EXCL to avoid TOCTOU races.
    Implements exponential backoff with stale lock detection.
    NEVER blocks forever — falls through with a warning after max_retries.

    Args:
        file_path: Absolute path to the file being locked.
        agent_name: Human-readable name of the agent acquiring the lock.
        max_retries: Maximum number of lock acquisition attempts.
        initial_backoff_ms: Starting backoff interval in milliseconds.
        max_backoff_ms: Maximum backoff interval in milliseconds.
        stale_timeout_s: Lock age threshold (seconds) for stale detection.

    Returns:
        FileLockResult with acquired=True on success, or
        acquired=False + timed_out=True after exhausting retries.
    """
    lock_path = file_path + ".lock"
    backoff_ms = initial_backoff_ms

    for attempt in range(max_retries):
        # Try to create lock file atomically (O_CREAT | O_EXCL)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            # We got the lock — write metadata
            metadata = {
                "pid": os.getpid(),
                "agent_name": agent_name,
                "timestamp": time.time(),
                "file_path": file_path,
            }
            os.write(fd, json.dumps(metadata).encode())
            os.close(fd)
            return FileLockResult(acquired=True, lock_path=lock_path)
        except FileExistsError:
            # Lock exists — check if stale
            existing = _read_lock_metadata(lock_path)
            if existing and _is_stale_lock(existing, stale_timeout_s):
                holder_pid = existing.get("pid", "?")
                logger.warning(
                    f"[FILE_LOCK] Stale lock detected for {file_path} "
                    f"(holder PID {holder_pid} is dead or lock age > "
                    f"{stale_timeout_s}s). Auto-releasing."
                )
                _force_release_lock(lock_path)
                continue  # Retry immediately after clearing stale lock

            # Not stale — log contention and backoff
            holder = existing.get("agent_name", "?") if existing else "?"
            logger.warning(
                f"[FILE_LOCK] File '{file_path}' locked by {holder} "
                f"(attempt {attempt + 1}/{max_retries}, waiting {backoff_ms}ms)"
            )
            time.sleep(backoff_ms / 1000.0)
            backoff_ms = min(backoff_ms * 2, max_backoff_ms)

    # Max retries exceeded — proceed with warning (NEVER block forever)
    logger.warning(
        f"[FILE_LOCK] TIMEOUT: Could not acquire lock for {file_path} "
        f"after {max_retries} retries. Proceeding with write."
    )
    return FileLockResult(acquired=False, lock_path=lock_path, timed_out=True)


def release_file_lock(lock_path: str) -> None:
    """Release a .lock sidecar file.

    Safe to call even if the lock was already released or never acquired.
    """
    try:
        os.unlink(lock_path)
    except FileNotFoundError:
        pass  # Already released — benign


def _read_lock_metadata(lock_path: str) -> dict | None:
    """Read and parse the JSON metadata from a .lock sidecar file.

    Returns None if the file is missing, empty, or contains invalid JSON.
    """
    try:
        with open(lock_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _is_stale_lock(metadata: dict, timeout_s: float) -> bool:
    """Determine if a lock is stale based on PID liveness or age.

    A lock is stale if:
      1. The holder PID no longer exists (ProcessLookupError), OR
      2. The lock age exceeds timeout_s seconds.

    Args:
        metadata: Parsed lock file JSON.
        timeout_s: Maximum age in seconds before a lock is considered stale.

    Returns:
        True if the lock should be auto-released.
    """
    # Check 1: Is the holder PID alive?
    pid = metadata.get("pid")
    if pid:
        try:
            os.kill(pid, 0)  # Signal 0 — doesn't kill, just checks existence
        except ProcessLookupError:
            return True  # PID doesn't exist — stale
        except PermissionError:
            pass  # Process exists but we can't signal it — not stale

    # Check 2: Lock age
    lock_time = metadata.get("timestamp", 0)
    if time.time() - lock_time > timeout_s:
        return True  # Too old — stale

    return False


def _force_release_lock(lock_path: str) -> None:
    """Force-remove a stale lock file.

    Best-effort: silently ignores errors if the file was already removed
    by another process racing to clear the same stale lock.
    """
    try:
        os.unlink(lock_path)
    except FileNotFoundError:
        pass  # Another process already cleared it — fine


class VariablesPlugin(ABC):
    @abstractmethod
    def get_variables(self, file: str, backup_dirs=None, **kwargs) -> dict[str, Any]:  # type: ignore
        """Return variable mappings for a prompt/markdown file.

        ``backup_dirs`` is intentionally left untyped at runtime to avoid use
        of ``list[str] | None`` PEP 604 unions, keeping this module compatible
        with Python 3.9 while remaining clear for type checkers in stubs.
        """
        pass


def load_plugin_variables(
    file: str, backup_dirs=None, **kwargs
) -> dict[str, Any]:
    if not file.endswith(".md"):
        return {}

    if backup_dirs is None:
        backup_dirs = []

    try:
        # Create filename and directories list
        plugin_filename = basename(file, ".md") + ".py"
        directories = [dirname(file)] + backup_dirs
        plugin_file = find_file_in_dirs(plugin_filename, directories)
    except FileNotFoundError:
        plugin_file = None

    if plugin_file and exists(plugin_file):

        from python.helpers import extract_tools

        classes = extract_tools.load_classes_from_file(
            plugin_file, VariablesPlugin, one_per_file=False
        )
        for cls in classes:
            return cls().get_variables(file, backup_dirs, **kwargs)  # type: ignore < abstract class here is ok, it is always a subclass

        # load python code and extract variables variables from it
        # module = None
        # module_name = dirname(plugin_file).replace("/", ".") + "." + basename(plugin_file, '.py')

        # try:
        #     spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        #     if not spec:
        #         return {}
        #     module = importlib.util.module_from_spec(spec)
        #     sys.modules[spec.name] = module
        #     spec.loader.exec_module(module)  # type: ignore
        # except ImportError:
        #     return {}

        # if module is None:
        #     return {}

        # # Get all classes in the module
        # class_list = inspect.getmembers(module, inspect.isclass)
        # # Filter for classes that are subclasses of VariablesPlugin
        # # iterate backwards to skip imported superclasses
        # for cls in reversed(class_list):
        #     if cls[1] is not VariablesPlugin and issubclass(cls[1], VariablesPlugin):
        #         return cls[1]().get_variables()  # type: ignore
    return {}





def parse_file(
    _filename: str, _directories=None, _encoding="utf-8", **kwargs
):
    if _directories is None:
        _directories = []

    # Find the file in the directories
    absolute_path = find_file_in_dirs(_filename, _directories)

    # Read the file content
    with open(absolute_path, "r", encoding=_encoding) as f:
        # content = remove_code_fences(f.read())
        content = f.read()

    is_json = is_full_json_template(content)
    content = remove_code_fences(content)
    variables = load_plugin_variables(absolute_path, _directories, **kwargs) or {}  # type: ignore
    variables.update(kwargs)
    if is_json:
        content = replace_placeholders_json(content, **variables)
        obj = json.loads(content)
        # obj = replace_placeholders_dict(obj, **variables)
        return obj
    else:
        content = replace_placeholders_text(content, **variables)
        # Process include statements
        content = process_includes(
            # here we use kwargs, the plugin variables are not inherited
            content,
            _directories,
            **kwargs,
        )
        return content


def read_prompt_file(
    _file: str, _directories=None, _encoding="utf-8", **kwargs
):
    if _directories is None:
        _directories = []

    # If filename contains folder path, extract it and add to directories
    if os.path.dirname(_file):
        folder_path = os.path.dirname(_file)
        _file = os.path.basename(_file)
        _directories = [folder_path] + _directories

    # Find the file in the directories
    absolute_path = find_file_in_dirs(_file, _directories)

    # Read the file content
    with open(absolute_path, "r", encoding=_encoding) as f:
        # content = remove_code_fences(f.read())
        content = f.read()

    variables = load_plugin_variables(_file, _directories, **kwargs) or {}  # type: ignore
    variables.update(kwargs)

    # Replace placeholders with values from kwargs
    content = replace_placeholders_text(content, **variables)

    # Process include statements
    content = process_includes(
        # here we use kwargs, the plugin variables are not inherited
        content,
        _directories,
        **kwargs,
    )

    return content


def read_file(relative_path: str, encoding="utf-8"):
    # Try to get the absolute path for the file from the original directory or backup directories
    absolute_path = get_abs_path(relative_path)

    # Read the file content
    with open(absolute_path, "r", encoding=encoding) as f:
        return f.read()


def read_file_bin(relative_path: str):
    # Try to get the absolute path for the file from the original directory or backup directories
    absolute_path = get_abs_path(relative_path)

    # read binary content
    with open(absolute_path, "rb") as f:
        return f.read()


def read_file_base64(relative_path):
    # get absolute path
    absolute_path = get_abs_path(relative_path)

    # read binary content and encode to base64
    with open(absolute_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def replace_placeholders_text(_content: str, **kwargs):
    # Replace placeholders with values from kwargs
    for key, value in kwargs.items():
        placeholder = "{{" + key + "}}"
        strval = str(value)
        _content = _content.replace(placeholder, strval)
    return _content


def replace_placeholders_json(_content: str, **kwargs):
    # Replace placeholders with values from kwargs
    for key, value in kwargs.items():
        placeholder = "{{" + key + "}}"
        strval = json.dumps(value)
        _content = _content.replace(placeholder, strval)
    return _content


def replace_placeholders_dict(_content: dict, **kwargs):
    def replace_value(value):
        if isinstance(value, str):
            placeholders = re.findall(r"{{(\w+)}}", value)
            if placeholders:
                for placeholder in placeholders:
                    if placeholder in kwargs:
                        replacement = kwargs[placeholder]
                        if value == f"{{{{{placeholder}}}}}":
                            return replacement
                        elif isinstance(replacement, (dict, list)):
                            value = value.replace(
                                f"{{{{{placeholder}}}}}", json.dumps(replacement)
                            )
                        else:
                            value = value.replace(
                                f"{{{{{placeholder}}}}}", str(replacement)
                            )
            return value
        elif isinstance(value, dict):
            return {k: replace_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [replace_value(item) for item in value]
        else:
            return value

    return replace_value(_content)


def process_includes(_content: str, _directories: list[str], **kwargs):
    # Regex to find {{ include 'path' }} or {{include'path'}}
    include_pattern = re.compile(r"{{\s*include\s*['\"](.*?)['\"]\s*}}")

    def replace_include(match):
        include_path = match.group(1)
        # if the path is absolute, do not process it
        if os.path.isabs(include_path):
            return match.group(0)
        # Search for the include file in the directories
        try:
            included_content = read_prompt_file(include_path, _directories, **kwargs)
            return included_content
        except FileNotFoundError:
            return match.group(0)  # Return original if file not found

    # Replace all includes with the file content
    return re.sub(include_pattern, replace_include, _content)


def find_file_in_dirs(_filename: str, _directories: list[str]):
    """
    This function searches for a filename in a list of directories in order.
    Returns the absolute path of the first found file.
    """
    # Loop through the directories in order
    for directory in _directories:
        # Create full path
        full_path = get_abs_path(directory, _filename)
        if exists(full_path):
            return full_path

    # If the file is not found, raise FileNotFoundError
    raise FileNotFoundError(
        f"File '{_filename}' not found in any of the provided directories: {_directories}"
    )


def get_unique_filenames_in_dirs(dir_paths: list[str], pattern: str = "*"):
    # returns absolute paths for unique filenames, priority by order in dir_paths
    seen = set()
    result = []
    for dir_path in dir_paths:
        full_dir = get_abs_path(dir_path)
        for file_path in glob.glob(os.path.join(full_dir, pattern)):
            fname = os.path.basename(file_path)
            if fname not in seen and os.path.isfile(file_path):
                seen.add(fname)
                result.append(get_abs_path(file_path))
    # sort by filename (basename), not the full path
    result.sort(key=lambda path: os.path.basename(path))
    return result


def remove_code_fences(text):
    # Pattern to match code fences with optional language specifier
    pattern = r"(```|~~~)(.*?\n)(.*?)(\1)"

    # Function to replace the code fences
    def replacer(match):
        return match.group(3)  # Return the code without fences

    # Use re.DOTALL to make '.' match newlines
    result = re.sub(pattern, replacer, text, flags=re.DOTALL)

    return result


def is_full_json_template(text):
    # Pattern to match the entire text enclosed in ```json or ~~~json fences
    pattern = r"^\s*(```|~~~)\s*json\s*\n(.*?)\n\1\s*$"
    # Use re.DOTALL to make '.' match newlines
    match = re.fullmatch(pattern, text.strip(), flags=re.DOTALL)
    return bool(match)


def write_file(relative_path: str, content: str, encoding: str = "utf-8"):
    from python.helpers.strings import sanitize_string
    abs_path = get_abs_path(relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    content = sanitize_string(content, encoding)
    with open(abs_path, "w", encoding=encoding) as f:
        f.write(content)


def write_file_atomic(
    relative_path: str,
    content: str,
    encoding: str = "utf-8",
    agent_name: str = "unknown",
):
    """
    Write content to a file atomically using a temp-and-rename pattern.
    Ensures that the destination file is never in a partially-written state.

    Uses .lock sidecar file coordination to serialize concurrent writes
    from multiple agents. The lock is always released in a finally block,
    even if the write raises an exception.

    Args:
        relative_path: Path to the file (relative or absolute).
        content: String content to write.
        encoding: File encoding (default utf-8).
        agent_name: Name of the agent performing the write (for lock metadata).
    """
    from python.helpers.strings import sanitize_string
    abs_path = get_abs_path(relative_path)
    dir_path = os.path.dirname(abs_path)
    os.makedirs(dir_path, exist_ok=True)

    # Acquire cross-agent file lock
    lock_result = acquire_file_lock(abs_path, agent_name=agent_name)
    try:
        content = sanitize_string(content, encoding)

        # Use a temp file in the same directory to ensure it's on the same filesystem
        # This is critical for os.replace to be atomic.
        fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())  # Force commit to disk

            # Atomically replace target file
            os.replace(temp_path, abs_path)
        except Exception as e:
            # Cleanup temp file on failure
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass  # Best-effort temp file cleanup
            raise e
    finally:
        # ALWAYS release the lock — even on write failure
        if lock_result.acquired:
            release_file_lock(lock_result.lock_path)



def save_json_atomic(abs_path: str, data: Any, indent: int = 2) -> None:
    """Write JSON data atomically using temp-and-rename pattern.

    Same safety guarantees as write_file_atomic but specialized for JSON:
    - Serializes to temp file first
    - Atomic rename to target via os.replace()
    - fsync for durability
    - Cleanup on failure — no orphaned temp files

    No external dependencies (no lockfile library, no git).

    Args:
        abs_path: Absolute path to the target JSON file.
        data: Any JSON-serializable object.
        indent: JSON indentation level (default 2).

    Raises:
        TypeError: If data is not JSON-serializable.
        OSError: If the write/rename fails after cleanup.
    """
    dir_path = os.path.dirname(abs_path)
    os.makedirs(dir_path, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        # Atomically replace target file
        os.replace(temp_path, abs_path)
    except Exception as e:
        # Cleanup temp file on failure
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass  # Best-effort cleanup
        raise e


def write_file_bin(relative_path: str, content: bytes):
    abs_path = get_abs_path(relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(content)


def write_file_base64(relative_path: str, content: str):
    # decode base64 string to bytes
    data = decode_base64(content)
    abs_path = get_abs_path(relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(data)


def decode_base64(content: str) -> bytes:
    # decode base64 string to bytes
    return base64.b64decode(content)


def delete_dir(relative_path: str) -> bool:
    """Delete a directory robustly with retry logic and logging.
    
    Uses a 3-attempt retry loop with delay to handle transient file locks
    (e.g., agent CWD, git operations, Docker mount timing).
    Logs warnings on failure instead of silently ignoring.
    
    Returns:
        True if the directory was deleted (or didn't exist), False if it persists.
    """
    import time
    import stat
    from python.helpers.print_style import PrintStyle
    
    abs_path = get_abs_path(relative_path)
    if not os.path.exists(abs_path):
        return True  # Nothing to delete — success
    
    def _onerror(func, path, exc_info):
        """Handle rmtree errors by fixing permissions and retrying."""
        try:
            os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            func(path)
        except Exception:
            pass  # Will be caught by retry loop

    max_attempts = 3
    delay = 0.5  # seconds between retry attempts
    
    for attempt in range(1, max_attempts + 1):
        try:
            shutil.rmtree(abs_path, onerror=_onerror)
        except Exception as e:
            PrintStyle.warning(
                f"[delete_dir] attempt {attempt}/{max_attempts} failed for "
                f"'{abs_path}': {e}"
            )
        
        if not os.path.exists(abs_path):
            return True  # Successfully deleted
        
        if attempt < max_attempts:
            PrintStyle.warning(
                f"[delete_dir] Directory still exists after attempt "
                f"{attempt}/{max_attempts}, retrying in {delay}s..."
            )
            time.sleep(delay)
    
    # Final warning — directory could not be deleted
    PrintStyle.warning(
        f"[delete_dir] FAILED to delete '{abs_path}' after "
        f"{max_attempts} attempts. Directory still exists. "
        f"Possible file lock or mount issue."
    )
    return False


def move_dir(old_path: str, new_path: str):
    # rename/move the directory from old_path to new_path (both relative)
    abs_old = get_abs_path(old_path)
    abs_new = get_abs_path(new_path)
    if not os.path.isdir(abs_old):
        return  # nothing to rename
    try:
        os.rename(abs_old, abs_new)
    except Exception:
        pass  # suppress all errors, keep behavior consistent


# move dir safely, remove with number if needed
def move_dir_safe(src, dst, rename_format="{name}_{number}"):
    base_dst = dst
    i = 2
    while exists(dst):
        dst = rename_format.format(name=base_dst, number=i)
        i += 1
    move_dir(src, dst)
    return dst


# create dir safely, add number if needed
def create_dir_safe(dst, rename_format="{name}_{number}"):
    base_dst = dst
    i = 2
    while exists(dst):
        dst = rename_format.format(name=base_dst, number=i)
        i += 1
    create_dir(dst)
    return dst


def create_dir(relative_path: str):
    abs_path = get_abs_path(relative_path)
    os.makedirs(abs_path, exist_ok=True)


def list_files(relative_path: str, filter: str = "*"):
    abs_path = get_abs_path(relative_path)
    if not os.path.exists(abs_path):
        return []
    return [file for file in os.listdir(abs_path) if fnmatch(file, filter)]


def make_dirs(relative_path: str):
    abs_path = get_abs_path(relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)


def get_abs_path(*relative_paths):
    "Convert relative paths to absolute paths based on the base directory."
    return os.path.join(get_base_dir(), *relative_paths)


def deabsolute_path(path: str):
    "Convert absolute paths to relative paths based on the base directory."
    return os.path.relpath(path, get_base_dir())


def fix_dev_path(path: str):
    """Normalize virtual paths (/agix/, /agix/, /root/) to absolute local paths.
    
    In Docker, the agent's CWD is /root/, so code_execution may save files
    there (e.g. /root/generated_image.png). We remap /root/ paths to the
    base directory so they can be served by image_get and other APIs.
    """
    # Convert legacy /agix/ prefix to current /agix/
    if path.startswith("/agix/"):
        path = path.replace("/agix/", "/agix/", 1)

    # Remap /root/ paths to base_dir — agent CWD in Docker is /root/
    # Files saved by code_execution land here instead of in /agix/
    if path.startswith("/root/"):
        path = path.replace("/root/", "", 1)

    # Strip /agix/ prefix to make it relative to base_dir
    # This works in both dev (local root) and prod (/agix root)
    if path.startswith("/agix/"):
        path = path.replace("/agix/", "", 1)

    return get_abs_path(path)


def normalize_agix_path(path: str):
    "Convert absolute paths into /agix/... paths"
    if is_in_base_dir(path):
        deabs = deabsolute_path(path)
        return "/agix/" + deabs
    return path


def normalize_agix_path(path: str):
    "Legacy alias for normalize_agix_path"
    return normalize_agix_path(path)


def exists(*relative_paths):
    path = get_abs_path(*relative_paths)
    return os.path.exists(path)


def get_base_dir():
    # Get the base directory from the current file path
    base_dir = os.path.dirname(os.path.abspath(os.path.join(__file__, "../../")))
    return base_dir


def basename(path: str, suffix=None):
    if suffix:
        return os.path.basename(path).removesuffix(suffix)
    return os.path.basename(path)


def dirname(path: str):
    return os.path.dirname(path)


def is_in_base_dir(path: str):
    # check if the given path is within the base directory
    base_dir = get_base_dir()
    # normalize paths to handle relative paths and symlinks
    abs_path = os.path.abspath(path)
    # check if the absolute path starts with the base directory
    return os.path.commonpath([abs_path, base_dir]) == base_dir


def get_subdirectories(
    relative_path: str,
    include="*",
    exclude=None,
):
    abs_path = get_abs_path(relative_path)
    if not os.path.exists(abs_path):
        return []
    if isinstance(include, str):
        include = [include]
    if isinstance(exclude, str):
        exclude = [exclude]
    return [
        subdir
        for subdir in os.listdir(abs_path)
        if os.path.isdir(os.path.join(abs_path, subdir))
        and any(fnmatch(subdir, inc) for inc in include)
        and (exclude is None or not any(fnmatch(subdir, exc) for exc in exclude))
    ]


def zip_dir(dir_path: str):
    full_path = get_abs_path(dir_path)
    zip_file_path = tempfile.NamedTemporaryFile(suffix=".zip", delete=False).name
    base_name = os.path.basename(full_path)
    with zipfile.ZipFile(zip_file_path, "w", compression=zipfile.ZIP_DEFLATED) as zip:
        for root, _, files in os.walk(full_path):
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, full_path)
                zip.write(file_path, os.path.join(base_name, rel_path))
    return zip_file_path


def move_file(relative_path: str, new_path: str):
    abs_path = get_abs_path(relative_path)
    new_abs_path = get_abs_path(new_path)
    os.makedirs(os.path.dirname(new_abs_path), exist_ok=True)
    os.rename(abs_path, new_abs_path)


def safe_file_name(filename: str) -> str:
    # Replace any character that's not alphanumeric, dash, underscore, or dot with underscore
    return re.sub(r"[^a-zA-Z0-9-._]", "_", filename)


def read_text_files_in_dir(
    dir_path: str, max_size: int = 1024 * 1024
) -> dict[str, str]:

    abs_path = get_abs_path(dir_path)
    if not os.path.exists(abs_path):
        return {}
    result = {}
    for file_path in [os.path.join(abs_path, f) for f in os.listdir(abs_path)]:
        try:
            if not os.path.isfile(file_path):
                continue
            if os.path.getsize(file_path) > max_size:
                continue
            mime, _ = mimetypes.guess_type(file_path)
            if mime is not None and not (mime.startswith("text") or mime == "application/json" or mime == "application/xml"):
                continue
            # Check if file is binary by reading a small chunk
            content = read_file(file_path)
            result[os.path.basename(file_path)] = content
        except Exception as e:
            logger.warning(f"[FILES] Skipped unreadable file {file_path}: {e}")
            continue
    return result

def list_files_in_dir_recursively(relative_path: str) -> list[str]:
    abs_path = get_abs_path(relative_path)
    if not os.path.exists(abs_path):
        return []
    result = []
    for root, dirs, files in os.walk(abs_path):
        for file in files:
            file_path = os.path.join(root, file)
            # Return relative path from the base directory
            rel_path = os.path.relpath(file_path, abs_path)
            result.append(rel_path)
    return result
    
