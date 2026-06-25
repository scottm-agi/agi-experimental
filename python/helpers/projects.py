from __future__ import annotations
import datetime
import json
import os
from typing import Literal, TypedDict, TYPE_CHECKING

from python.helpers import files, dirty_json, persist_chat, file_tree
from python.helpers.print_style import PrintStyle
from python.helpers.mise_manager import MiseManager


if TYPE_CHECKING:
    from python.agent import AgentContext

PROJECTS_PARENT_DIR = files.get_abs_path("usr/projects")
PROJECT_META_DIR = ".agix.proj"
LEGACY_PROJECT_META_DIR = ".agix.proj"
PROJECT_INSTRUCTIONS_DIR = "instructions"
PROJECT_KNOWLEDGE_DIR = "knowledge"
PROJECT_HEADER_FILE = "project.json"
PROJECT_MEMORY_BANK_DIR = "memory-bank"

CONTEXT_DATA_KEY_PROJECT = "project"

# ── System of Record: Decomposition Index ──
# The decomposition_index.json is THE authoritative record of phase progress.
# Canonical location: {project_dir}/docs/decomposition_index.json
# Legacy location:    {project_dir}/decomposition_index.json (read-only fallback)
# All writes MUST go to the canonical location. All reads check canonical first,
# then legacy fallback. In-memory caches (_current_phase etc.) must be derived
# from this file, not the other way around.
DECOMPOSITION_INDEX_FILENAME = "decomposition-index.json"
DECOMPOSITION_INDEX_SUBDIR = "docs"


def get_decomp_index_path(project_dir: str, *, for_write: bool = False) -> str:
    """Get the path to decomposition-index.json for a project.

    Args:
        project_dir: Absolute path to the project directory.
        for_write: If True, creates the docs/ directory if needed.

    Returns:
        Absolute path to docs/decomposition-index.json.
    """
    canonical = os.path.join(project_dir, DECOMPOSITION_INDEX_SUBDIR, DECOMPOSITION_INDEX_FILENAME)

    if for_write:
        docs_dir = os.path.join(project_dir, DECOMPOSITION_INDEX_SUBDIR)
        os.makedirs(docs_dir, exist_ok=True)

    return canonical


class FileStructureInjectionSettings(TypedDict):
    enabled: bool
    max_depth: int
    max_files: int
    max_folders: int
    max_lines: int
    gitignore: str


class BasicProjectData(TypedDict):
    title: str
    description: str
    instructions: str
    color: str
    memory: Literal[
        "own", "global"
    ]  # in the future we can add cutom and point to another existing folder
    file_structure: FileStructureInjectionSettings


class EditProjectData(BasicProjectData):
    name: str
    instruction_files_count: int
    knowledge_files_count: int
    secrets: str
    parameters: str


def get_projects_parent_folder():
    return files.get_abs_path(PROJECTS_PARENT_DIR)


def get_project_folder(name: str):
    return files.get_abs_path(get_projects_parent_folder(), name)


def get_project_meta_folder(name: str, *sub_dirs: str, legacy_check: bool = True):
    """
    Get the project metadata folder.
    If legacy_check is True, it will return the legacy .agix.proj folder if it exists 
    and the new .agix.proj folder does not.
    """
    project_folder = get_project_folder(name)
    new_meta = files.get_abs_path(project_folder, PROJECT_META_DIR, *sub_dirs)
    
    if legacy_check:
        if not os.path.exists(new_meta):
            legacy_meta = files.get_abs_path(project_folder, LEGACY_PROJECT_META_DIR, *sub_dirs)
            if os.path.exists(legacy_meta):
                return legacy_meta
                
    return new_meta


async def delete_project(name: str) -> dict:
    """Delete a project by name.
    
    Delegates to LifecycleService.delete_project() which handles:
    1. Deactivating all in-memory chats referencing this project
    2. Cascade-deleting ALL SQL context entries
    3. Deleting the project filesystem directory
    4. Cleaning up scoped secrets/parameters
    
    Returns:
        dict with keys:
        - name (str): project name
        - deleted (bool): True if filesystem dir was removed
        - warning (str|None): warning message if deletion had issues
    """
    if name.lower() in ("default", "agixdashboard"):
        PrintStyle.warning(f"Cannot delete built-in project: {name}")
        return {"name": name, "deleted": False, "warning": f"Cannot delete built-in project: {name}"}

    # Route through centralized LifecycleService — single authoritative
    # deletion path for project + associated chats + filesystem + scope.
    from python.helpers.lifecycle_service import LifecycleService
    result = await LifecycleService.delete_project(name)

    abs_path = files.get_abs_path(PROJECTS_PARENT_DIR, name)
    warning = None
    if os.path.exists(abs_path):
        warning = (
            f"Directory '{abs_path}' still exists after deletion attempt. "
            f"Possible file lock or mount issue."
        )
        PrintStyle.warning(f"[delete_project] WARNING: {warning}")
    else:
        PrintStyle.info(f"[delete_project] Successfully deleted: {abs_path}")

    return {"name": name, "deleted": result.get("directory", False), "warning": warning}


def get_upload_folder(project_name: str | None) -> str:
    """
    Get the upload folder path based on active project.
    
    When a project is active, uploads are stored per-project:
        usr/projects/<name>/uploads/
    Otherwise, fall back to the global upload directory:
        tmp/uploads/
    
    Args:
        project_name: Active project name, or None/empty for global.
        
    Returns:
        Absolute path to the upload folder (external/development path).
    """
    if project_name:
        project_folder = get_project_folder(project_name)
        return os.path.join(project_folder, "uploads")
    return files.get_abs_path("tmp/uploads")


def get_upload_folder_internal(project_name: str | None) -> str:
    """
    Get the internal (Docker) upload folder path based on active project.
    
    Args:
        project_name: Active project name, or None/empty for global.
        
    Returns:
        Internal path for Docker use.
    """
    if project_name:
        return f"/agix/usr/projects/{project_name}/uploads"
    if os.path.isdir("/agix"):
        return "/agix/tmp/uploads"
    return "/agix/tmp/uploads"


def create_project(name: str, data: BasicProjectData):
    abs_path = files.create_dir_safe(
        files.get_abs_path(PROJECTS_PARENT_DIR, name), rename_format="{name}_{number}"
    )
    create_project_meta_folders(name)
    data = _normalizeBasicData(data)
    save_project_header(name, data)
    return name


def load_project_header(name: str):
    """
    Load project header from disk with memory caching.
    """
    import os
    import time

    global _project_header_cache
    if '_project_header_cache' not in globals():
        globals()['_project_header_cache'] = {}

    cache = globals()['_project_header_cache']
    current_time = time.time()

    meta_folder = get_project_meta_folder(name)
    abs_path = os.path.join(meta_folder, PROJECT_HEADER_FILE)
    
    # Check cache
    if name in cache:
        cached_data, cached_time, last_mtime = cache[name]
        # Valid for 30s if file hasn't changed
        if current_time - cached_time < 30:
            try:
                # OPTIMIZATION: Trust cache for 2 seconds without even checking mtime
                # This significantly reduces disk I/O during high-frequency polls
                if current_time - cached_time < 2:
                    return cached_data

                if os.path.exists(abs_path) and os.path.getmtime(abs_path) == last_mtime:
                    # Update cache timestamp to extend trust window even if file hasn't changed
                    cache[name] = (cached_data, current_time, last_mtime)
                    return cached_data
            except Exception:
                pass

    if not os.path.exists(abs_path):
        return {}

    try:
        mtime = os.path.getmtime(abs_path)
        header: dict = dirty_json.parse(files.read_file(abs_path))  # type: ignore
        header["name"] = name
        
        # Update cache
        cache[name] = (header, current_time, mtime)
        return header
    except Exception as e:
        PrintStyle.debug(f"Error loading project header for {name}: {e}")
        return {"name": name}



def _default_file_structure_settings():
    try:
        gitignore = files.read_file("conf/projects.default.gitignore")
    except Exception:
        gitignore = ""
    return FileStructureInjectionSettings(
        enabled=True,
        max_depth=5,
        max_files=20,
        max_folders=20,
        max_lines=250,
        gitignore=gitignore,
    )


def _normalizeFileStructure(data: dict | None) -> FileStructureInjectionSettings:
    defaults = _default_file_structure_settings()
    if not data:
        return defaults

    return FileStructureInjectionSettings(
        enabled=data.get("enabled", defaults["enabled"]),
        max_depth=data.get("max_depth", defaults["max_depth"]),
        max_files=data.get("max_files", defaults["max_files"]),
        max_folders=data.get("max_folders", defaults["max_folders"]),
        max_lines=data.get("max_lines", defaults["max_lines"]),
        gitignore=data.get("gitignore", defaults["gitignore"]),
    )


def _normalizeBasicData(data: BasicProjectData):
    return BasicProjectData(
        title=data.get("title", ""),
        description=data.get("description", ""),
        instructions=data.get("instructions", ""),
        color=data.get("color", ""),
        memory=data.get("memory", "own"),
        file_structure=_normalizeFileStructure(data.get("file_structure")),  # type: ignore
    )


def _normalizeEditData(data: EditProjectData):
    return EditProjectData(
        name=data.get("name", ""),
        title=data.get("title", ""),
        description=data.get("description", ""),
        instructions=data.get("instructions", ""),
        color=data.get("color", ""),
        instruction_files_count=data.get("instruction_files_count", 0),
        knowledge_files_count=data.get("knowledge_files_count", 0),
        secrets=data.get("secrets", ""),
        parameters=data.get("parameters", "{}"),
        memory=data.get("memory", "own"),
        file_structure=_normalizeFileStructure(data.get("file_structure")),  # type: ignore
    )


def _edit_data_to_basic_data(data: EditProjectData):
    return _normalizeBasicData(data)


def _basic_data_to_edit_data(data: BasicProjectData):
    return _normalizeEditData(data)  # type: ignore


def update_project(name: str, data: EditProjectData):
    # merge with current state
    current = load_edit_project_data(name)
    current.update(data)
    current = _normalizeEditData(current)

    # save header data
    header = _edit_data_to_basic_data(current)
    save_project_header(name, header)

    save_project_secrets(name, current["secrets"])
    save_project_parameters(name, current["parameters"])

    # This is a sync call in update_project, so we need to run it in a loop if possible
    # but update_project is usually called from an async API handler.
    # However, if it's not, we'll use loop.run_until_complete.
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(reactivate_project_in_chats(name))
        else:
            asyncio.run(reactivate_project_in_chats(name))
    except Exception:
        pass
    return name


def load_basic_project_data(name: str) -> BasicProjectData:
    data = BasicProjectData(**load_project_header(name))
    normalized = _normalizeBasicData(data)
    return normalized


def load_edit_project_data(name: str) -> EditProjectData:
    data = load_basic_project_data(name)
    additional_instructions = get_additional_instructions_files(
        name
    )  # for additional info
    instruction_files_count = len(additional_instructions)
    knowledge_files_count = get_knowledge_files_count(name)
    secrets = load_project_secrets_masked(name)
    parameters = load_project_parameters_json(name)
    
    data = EditProjectData(
        **data,
        name=name,
        instruction_files_count=instruction_files_count,
        knowledge_files_count=knowledge_files_count,
        secrets=secrets,
        parameters=parameters,
    )
    data = _normalizeEditData(data)
    return data


def save_project_header(name: str, data: BasicProjectData):
    # save project header file
    header = dirty_json.stringify(data)
    meta_folder = get_project_meta_folder(name)
    abs_path = os.path.join(meta_folder, PROJECT_HEADER_FILE)

    files.write_file(abs_path, header)


_last_orphan_cleanup_time: float = 0
_ORPHAN_CLEANUP_THROTTLE_SECONDS = 60
MIN_ORPHAN_AGE_SECONDS = 43200  # M-3 Fix: 12 hours (was 1 hour) — don't delete dirs younger than this


def get_active_projects_list():
    global _last_orphan_cleanup_time
    ensure_default_project_exists()
    ensure_dashboard_project_exists()

    # Throttled orphan cleanup — runs at most once per 60 seconds
    import time
    now = time.time()
    if now - _last_orphan_cleanup_time >= _ORPHAN_CLEANUP_THROTTLE_SECONDS:
        _last_orphan_cleanup_time = now
        try:
            cleanup_orphan_project_dirs()
        except Exception as e:
            PrintStyle.warning(f"[get_active_projects_list] Orphan cleanup failed: {e}")

    return _get_projects_list(get_projects_parent_folder())


def _get_projects_list(parent_dir):
    projects = []

    # folders in project directory
    for name in os.listdir(parent_dir):
        try:
            abs_path = os.path.join(parent_dir, name)
            if os.path.isdir(abs_path):
                # Check for project header file existence to avoid noise
                header_folder = get_project_meta_folder(name)
                header_path = os.path.join(header_folder, PROJECT_HEADER_FILE)
                if not os.path.exists(header_path):
                    continue
                
                project_data = load_basic_project_data(name)

                # Get filesystem timestamps from the header file
                created_at = ""
                updated_at = ""
                try:
                    stat = os.stat(header_path)
                    created_at = datetime.datetime.fromtimestamp(
                        stat.st_birthtime if hasattr(stat, 'st_birthtime') else stat.st_ctime,
                        tz=datetime.timezone.utc
                    ).isoformat()
                    updated_at = datetime.datetime.fromtimestamp(
                        stat.st_mtime, tz=datetime.timezone.utc
                    ).isoformat()
                except Exception:
                    pass

                projects.append(
                    {
                        "name": name,
                        "title": project_data.get("title", ""),
                        "description": project_data.get("description", ""),
                        "color": project_data.get("color", ""),
                        "created_at": created_at,
                        "updated_at": updated_at,
                    }
                )
        except Exception as e:
            PrintStyle.error(f"Error loading project {name}: {str(e)}")

    # sort projects by name
    projects.sort(key=lambda x: x["name"])
    return projects


def _get_newest_file_mtime(dir_path: str, max_depth: int = 3) -> float:
    """Get the most recent file modification time in a directory tree.
    
    M-3 Fix: Check actual file activity instead of just directory mtime.
    A directory's mtime only updates when files are added/removed from IT,
    not when files inside it are modified.
    
    Args:
        dir_path: Root directory to scan.
        max_depth: Maximum recursion depth to limit scan time.
    
    Returns:
        Most recent mtime found, or 0.0 if no files found.
    """
    newest = 0.0
    try:
        for depth, (root, dirs, fnames) in _walk_with_depth(dir_path):
            if depth >= max_depth:
                dirs.clear()  # Stop recursion
                continue
            for fname in fnames:
                try:
                    fpath = os.path.join(root, fname)
                    mt = os.path.getmtime(fpath)
                    if mt > newest:
                        newest = mt
                except OSError:
                    continue
    except OSError:
        pass
    return newest


def _walk_with_depth(top):
    """os.walk wrapper that yields (depth, (root, dirs, files))."""
    for root, dirs, files in os.walk(top):
        depth = root.replace(top, "").count(os.sep)
        yield depth, (root, dirs, files)


# M-3: Multi-factor activity signals that indicate a directory is NOT an orphan
# even if it has no project.json yet (agent is still scaffolding it).
_ACTIVITY_SIGNALS = [
    "package.json",
    ".git",
    "node_modules",
    ".scaffold_complete",
    "src",
    "app",
    "tsconfig.json",
    "next.config.js",
    "next.config.ts",
    "next.config.mjs",
]


def cleanup_orphan_project_dirs() -> list[str]:
    """Remove orphan project directories that have no valid project.json.
    
    M-3 Fix: Multi-factor identity check before deletion:
    1. Must lack project.json (existing check)
    2. Must lack ALL activity signals (package.json, .git, node_modules, etc.)
    3. Must be older than MIN_ORPHAN_AGE_SECONDS (12 hours)
    4. Must have no recently-modified files (newest file mtime check)
    
    Returns:
        list of orphan directory names that were removed.
    """
    import shutil
    import time as _time
    
    parent_dir = get_projects_parent_folder()
    if not os.path.exists(parent_dir):
        return []
    
    protected = {"default", "agixdashboard"}
    removed = []
    now = _time.time()
    
    for name in os.listdir(parent_dir):
        if name.lower() in protected:
            continue
            
        abs_path = os.path.join(parent_dir, name)
        if not os.path.isdir(abs_path):
            continue
        
        # Factor 1: Check both new and legacy meta dirs for project.json
        has_header = False
        for meta_dir_name in [PROJECT_META_DIR, LEGACY_PROJECT_META_DIR]:
            header_path = os.path.join(abs_path, meta_dir_name, PROJECT_HEADER_FILE)
            if os.path.exists(header_path):
                has_header = True
                break
        
        if has_header:
            continue
        
        # Factor 2 (M-3): Check for activity signals
        # If ANY signal file/dir exists, this is an active project being scaffolded
        has_activity_signal = False
        for signal in _ACTIVITY_SIGNALS:
            signal_path = os.path.join(abs_path, signal)
            if os.path.exists(signal_path):
                has_activity_signal = True
                PrintStyle.debug(
                    f"[cleanup_orphan] Skipping dir with activity signal: {name} "
                    f"(found {signal}, no project.json)"
                )
                break
        
        if has_activity_signal:
            continue
        
        # Factor 3: AGE CHECK using newest file mtime (M-3 fix)
        # Use newest file mtime instead of just dir mtime to catch active work
        try:
            newest_mtime = _get_newest_file_mtime(abs_path, max_depth=3)
            dir_mtime = os.path.getmtime(abs_path)
            effective_mtime = max(newest_mtime, dir_mtime)
            age_seconds = now - effective_mtime
            if age_seconds < MIN_ORPHAN_AGE_SECONDS:
                PrintStyle.debug(
                    f"[cleanup_orphan] Skipping young orphan dir: {name} "
                    f"(age={age_seconds:.0f}s < {MIN_ORPHAN_AGE_SECONDS}s, no project.json)"
                )
                continue
        except OSError:
            continue  # Can't stat — skip rather than risk deleting active work
        
        PrintStyle.warning(
            f"[cleanup_orphan] Removing orphan project dir: {name} "
            f"(no project.json, no activity signals, age={age_seconds:.0f}s)"
        )
        try:
            if files.delete_dir(abs_path):
                removed.append(name)
            else:
                PrintStyle.error(
                    f"[cleanup_orphan] Failed to remove orphan '{name}' after retries"
                )
        except Exception as e:
            PrintStyle.error(
                f"[cleanup_orphan] Failed to remove orphan '{name}': {e}"
            )
    
    if removed:
        PrintStyle.info(
            f"[cleanup_orphan] Removed {len(removed)} orphan dirs: {removed}"
        )
    return removed


def get_project_git_remote(name: str) -> str | None:
    """
    Get the git remote URL for a project by parsing .git/config.
    
    Args:
        name: Project name
        
    Returns:
        Remote URL if found, None otherwise
    """
    project_path = get_project_folder(name)
    git_config = os.path.join(project_path, ".git", "config")
    
    if not os.path.exists(git_config):
        return None
    
    try:
        with open(git_config, 'r') as f:
            content = f.read()
            # Parse git config for remote URL
            # Look for url = ... under [remote "origin"]
            import re
            match = re.search(r'\[remote\s+"origin"\][^\[]*url\s*=\s*(.+)', content, re.IGNORECASE)
            if match:
                return match.group(1).strip()
    except Exception:
        pass
    return None


def find_project_by_git_remote(remote_url: str) -> str | None:
    """
    Search all projects for one matching the given git remote URL.
    
    Issue #903: When multiple projects match (e.g., `repo-myapp` and `build-1144-conflict-c`),
    prefer the canonical `repo-{name}` project over build-specific or temporary ones.
    
    Args:
        remote_url: Git remote URL (e.g., https://forgejo.example.com/user/repo.git)
        
    Returns:
        Project name if found, None otherwise
    """
    if not remote_url:
        return None
    
    # Normalize URL for comparison (strip .git, trailing slashes, lowercase)
    def normalize_url(url: str) -> str:
        return url.rstrip('/').replace('.git', '').lower()
    
    normalized_target = normalize_url(remote_url)
    
    projects_dir = get_projects_parent_folder()
    if not os.path.exists(projects_dir):
        return None
    
    # Collect ALL matching projects, then pick the best one
    matches = []
    for name in os.listdir(projects_dir):
        try:
            project_path = os.path.join(projects_dir, name)
            if not os.path.isdir(project_path):
                continue
                
            project_remote = get_project_git_remote(name)
            if project_remote and normalize_url(project_remote) == normalized_target:
                matches.append(name)
        except Exception:
            continue
    
    if not matches:
        return None
    
    if len(matches) == 1:
        return matches[0]
    
    # Issue #903: Prefer canonical project names over build-specific ones
    # Priority: repo-{name} > exact repo name > anything else
    for m in matches:
        if m.lower().startswith("repo-"):
            return m
    
    # Prefer projects that DON'T start with "build-" (stale build artifacts)
    non_build = [m for m in matches if not m.lower().startswith("build-")]
    if non_build:
        return non_build[0]
    
    # All are build projects — return the most recently modified one
    def get_mtime(name):
        try:
            return os.path.getmtime(os.path.join(projects_dir, name))
        except Exception:
            return 0
    
    return max(matches, key=get_mtime)


def get_project_memory_bank_folder(name: str):
    return os.path.join(get_project_folder(name), PROJECT_MEMORY_BANK_DIR)


def get_project_tmp_folder(name: str):
    return os.path.join(get_project_folder(name), "tmp")


async def activate_project(context_id: str, name: str):
    from python.agent import AgentContext

    # Ensure project exists before activation (especially for 'default')
    if name.lower() == "default":
        ensure_default_project_exists()
        name = "default"

    # Try to find the project case-insensitively if direct load fails
    project_name = name
    if not os.path.exists(get_project_folder(name)):
        projects_list = get_active_projects_list()
        for p in projects_list:
            if p["name"].lower() == name.lower():
                project_name = p["name"]
                break

    # GUARD: Verify the project actually exists (has project.json).
    # This prevents resurrection of deleted projects when persisted chats
    # try to reactivate a project that was deleted but whose chat contexts
    # were not fully deactivated before shutdown.
    meta_folder = get_project_meta_folder(project_name, legacy_check=True)
    header_path = os.path.join(meta_folder, PROJECT_HEADER_FILE)
    if not os.path.exists(header_path):
        PrintStyle.warning(
            f"[activate_project] Project '{project_name}' does not exist "
            f"(no project.json at {header_path}). Skipping activation to "
            f"prevent resurrection of deleted project."
        )
        raise Exception(
            f"Project '{project_name}' does not exist or was deleted. "
            f"No such project found."
        )

    data = load_edit_project_data(project_name)
    context = AgentContext.get(context_id)
    if context is None:
        context = await persist_chat.load_chat(context_id)

    if context is None:
        raise Exception("Context not found")
    display_name = str(data.get("title", project_name))
    display_name = display_name[:22] + "..." if len(display_name) > 25 else display_name
    
    PrintStyle.info(f"Activating project: {project_name} for context: {context_id}")
    context.set_data(CONTEXT_DATA_KEY_PROJECT, project_name)
    context.set_output_data(
        CONTEXT_DATA_KEY_PROJECT,
        {"name": project_name, "title": display_name, "color": data.get("color", "")},
    )

    # Reinstall/refresh agent configuration with project metadata/parameters
    context.refresh_agents_config()

    # ensure memory bank and mise are initialized (mise-en-place)
    create_project_meta_folders(project_name)

    # persist
    persist_chat.save_tmp_chat(context)
    AgentContext._increment_version()
    PrintStyle.info(f"Project context saved for {context_id}")


async def deactivate_project(context_id: str):
    from python.agent import AgentContext

    context = AgentContext.get(context_id)
    if context is None:
        context = await persist_chat.load_chat(context_id)

    if context is None:
        raise Exception("Context not found")
    context.set_data(CONTEXT_DATA_KEY_PROJECT, None)
    context.set_output_data(CONTEXT_DATA_KEY_PROJECT, None)

    # Reinstall/refresh agent configuration (clearing project metadata)
    context.refresh_agents_config()

    # persist
    persist_chat.save_tmp_chat(context)
    AgentContext._increment_version()


async def reactivate_project_in_chats(name: str):
    from python.agent import AgentContext

    for context in AgentContext.all():
        if context.get_data(CONTEXT_DATA_KEY_PROJECT) == name:
            await activate_project(context.id, name)
            persist_chat.save_tmp_chat(context)


async def deactivate_project_in_chats(name: str):
    from python.agent import AgentContext

    for context in AgentContext.all():
        if context.get_data(CONTEXT_DATA_KEY_PROJECT) == name:
            await deactivate_project(context.id)
            persist_chat.save_tmp_chat(context)


def build_system_prompt_vars(name: str):
    project_data = load_basic_project_data(name)
    main_instructions = project_data.get("instructions", "") or ""
    additional_instructions = get_additional_instructions_files(name)
    complete_instructions = (
        main_instructions
        + "\n\n"
        + "\n\n".join(
            additional_instructions[k] for k in sorted(additional_instructions)
        )
    )

    # 1. Load GLOBAL agents.md (System Level)
    global_rules = get_global_agent_rules()
    if global_rules:
        complete_instructions += f"\n\n## Global System Rules (Standard agents.md)\n{global_rules}"

    # 2. Load PROJECT agents.md (Project Root Level)
    root_rules = get_project_root_rules(name)
    if root_rules:
        complete_instructions += f"\n\n## Project-Specific Rules (Local agents.md)\n{root_rules}"

    # 3. Discover repository automation context (context/ and knowledge/ folders)
    root_context = get_project_root_context(name)
    if root_context:
        explanation = (
            "\n### Source: Repository Automation Context\n"
            "The following information was discovered in the project's root `context/` or `knowledge/` directories. "
            "This content provides project-specific domain knowledge and automation guidance. "
            "It is distinct from internal system knowledge stored in `.agix.proj/`."
        )
        complete_instructions += f"\n\n{explanation}\n{root_context}"

    # 4. Project Memory Bank (memory-bank/*.md at root)
    mb_context = get_project_memory_bank_context(name)
    if mb_context:
        complete_instructions += f"\n\n## Project Memory Bank (Architectural Context)\n{mb_context}"

    complete_instructions = complete_instructions.strip()
    return {
        "project_name": project_data.get("title", ""),
        "project_description": project_data.get("description", ""),
        "project_instructions": complete_instructions or "",
        "project_path": files.normalize_agix_path(get_project_folder(name)),
    }


def get_global_agent_rules():
    rules_file = files.get_abs_path("agents.md")
    if os.path.exists(rules_file):
        try:
            return files.read_file(rules_file)
        except Exception:
            return ""
    return ""


def get_project_root_rules(name: str):
    root = get_project_folder(name)
    rules_file = os.path.join(root, "agents.md")
    if os.path.exists(rules_file):
        try:
            return files.read_file(rules_file)
        except Exception:
            return ""
    return ""


def get_project_root_context(name: str):
    root = get_project_folder(name)
    context_content = []

    # Check context/ and knowledge/ in root
    # Note: 'knowledge' in root is distinct from '.agix.proj/knowledge'
    # This is intended for repository automation purposes.
    for folder_name in ["context", "knowledge"]:
        folder_path = os.path.join(root, folder_name)
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            # Recursively read text files for better context coverage
            files_dict = {}
            for rel_file in files.list_files_in_dir_recursively(folder_path):
                abs_file = os.path.join(folder_path, rel_file)
                try:
                    # Skip non-text if we can identify them, but list_files_in_dir_recursively
                    # doesn't filter. We'll use read_file which is standard.
                    content = files.read_file(abs_file)
                    files_dict[rel_file] = content
                except Exception:
                    continue

            for fname, content in files_dict.items():
                context_content.append(f"### Context File ({folder_name}): {fname}\n{content}")

    return "\n\n".join(context_content)


def get_project_memory_bank_context(name: str):
    mb_dir = get_project_memory_bank_folder(name)
    if not os.path.exists(mb_dir):
        return ""
    
    contents = []
    # Read all .md files in memory-bank directory
    for filename in sorted(os.listdir(mb_dir)):
        if filename.endswith(".md"):
            filepath = os.path.join(mb_dir, filename)
            try:
                content = files.read_file(filepath)
                if content.strip():
                    contents.append(f"### File: {filename}\n{content}")
            except Exception:
                continue
    
    return "\n\n".join(contents)


def get_additional_instructions_files(name: str):
    meta_folder = get_project_meta_folder(name)
    instructions_folder = os.path.join(meta_folder, PROJECT_INSTRUCTIONS_DIR)
    if not os.path.exists(instructions_folder):
        return {}
    return files.read_text_files_in_dir(instructions_folder)


def get_context_project_name(context: "AgentContext") -> str | None:
    # Defensive check for Issue #278
    if not hasattr(context, "get_data"):
        return None
    name = context.get_data(CONTEXT_DATA_KEY_PROJECT)
    if name:
        # SS-11 L3 gate: verify project directory exists on disk.
        # Catches stale project references from prior smoke test runs
        # where a new project was created without deactivating the old one.
        project_dir = get_project_folder(name)
        if not os.path.isdir(project_dir):
            import logging
            _logger = logging.getLogger("agix.projects")
            _logger.warning(
                f"[PROJECT CONTEXT] Stale project reference '{name}' — "
                f"directory {project_dir} does not exist. Clearing."
            )
            context.set_data(CONTEXT_DATA_KEY_PROJECT, None)
            return None
    return name




def load_project_secrets_masked(name: str, merge_with_global=False):
    from python.helpers import secrets_helper as secrets

    mgr = secrets.get_project_secrets_manager(name, merge_with_global)
    return mgr.get_masked_secrets()


def save_project_secrets(name: str, secrets: str):
    from python.helpers.secrets_helper import get_project_secrets_manager

    secrets_manager = get_project_secrets_manager(name)
    secrets_manager.save_secrets_with_merge(secrets)


def load_project_parameters_json(name: str):
    from python.helpers.parameters import get_project_parameters_manager
    import json
    
    mgr = get_project_parameters_manager(name)
    params = mgr.load_parameters()
    return json.dumps(params, indent=4)


def save_project_parameters(name: str, parameters: str):
    from python.helpers.parameters import get_project_parameters_manager
    import json
    
    try:
        params_dict = json.loads(parameters)
        mgr = get_project_parameters_manager(name)
        mgr.save_parameters(params_dict)
    except Exception as e:
        PrintStyle.error(f"Error saving project parameters for {name}: {e}")


def get_context_memory_subdir(context: "AgentContext") -> str | None:
    # if a project is active and has memory isolation set, return the project memory subdir
    project_name = get_context_project_name(context)
    if project_name:
        project_data = load_basic_project_data(project_name)
        if project_data["memory"] == "own":
            return "projects/" + project_name
    return None  # no memory override


def create_project_meta_folders(name: str):
    # create instructions folder
    files.create_dir(get_project_meta_folder(name, PROJECT_INSTRUCTIONS_DIR))

    # create knowledge folders
    files.create_dir(get_project_meta_folder(name, PROJECT_KNOWLEDGE_DIR))
    from python.helpers import memory

    for memory_type in memory.Memory.Area:
        files.create_dir(
            get_project_meta_folder(name, PROJECT_KNOWLEDGE_DIR, memory_type.value)
        )
    
# create memory bank folder and initialize it
    initialize_project_memory_bank(name)
    
    # initialize mise environment
    initialize_project_mise(name)


def initialize_project_mise(name: str):
    project_path = get_project_folder(name)
    mgr = MiseManager(project_path)
    mgr.write_mise_toml(overwrite=False)
    mgr.write_gitignore(overwrite=False)


def ensure_default_project_exists():
    """
    Ensure the 'default' project exists. This project serves as a catch-all 
    for chats created without a specific project assignment.
    """
    default_name = "default"
    meta_folder = get_project_meta_folder(default_name)
    header_path = os.path.join(meta_folder, PROJECT_HEADER_FILE)
    
    if not os.path.exists(header_path):
        PrintStyle.info("Creating default catch-all project...")
        data = BasicProjectData(
            title="Default Project",
            description="Catch-all project for general tasks and chats without an assigned project.",
            instructions="This is the default project for general tasks.",
            color="#4f46e5", # Indigo
            memory="own",
            file_structure=_default_file_structure_settings()
        )
        create_project(default_name, data)


def ensure_dashboard_project_exists():
    """
    Ensure the 'agixdashboard' project exists. This project serves as a dedicated 
    system project for the A2UI dashboard to isolate its execution context.
    """
    dashboard_name = "agixdashboard"
    meta_folder = get_project_meta_folder(dashboard_name)
    header_path = os.path.join(meta_folder, PROJECT_HEADER_FILE)
    
    if not os.path.exists(header_path):
        PrintStyle.info("Creating built-in agixdashboard project...")
        data = BasicProjectData(
            title="System Dashboard",
            description="Built-in project for executing dashboard analytics queries.",
            instructions="This project is restricted for systemic dashboard execution.",
            color="#10b981", # Emerald
            memory="own",
            file_structure=_default_file_structure_settings()
        )
        create_project(dashboard_name, data)


def initialize_project_memory_bank(name: str):
    mb_dir = get_project_memory_bank_folder(name)
    _initialize_memory_bank_directory(mb_dir)

def initialize_global_memory_bank():
    mb_dir = files.get_abs_path("memory-bank")
    _initialize_memory_bank_directory(mb_dir)

def _initialize_memory_bank_directory(mb_dir: str):
    files.create_dir(mb_dir)
    
    required_files = {
        "projectbrief.md": "# Project Brief\n\nDefines project scope, goals, and high-level requirements.",
        "activeContext.md": "# Active Context\n\nTracks current focus, recent changes, and immediate next steps.",
        "progress.md": "# Progress\n\nLogs completed work, remaining tasks, and known issues.",
        "systemPatterns.md": "# System Patterns\n\nDocuments architecture, design patterns, and technical decisions.",
        "techContext.md": "# Tech Context\n\nDetails tech stack, dependencies, and project constraints.",
        "lessons-learned.md": "# Lessons Learned\n\nCaptures key lessons from mistakes, challenges, and solutions."
    }
    
    for filename, initial_content in required_files.items():
        filepath = os.path.join(mb_dir, filename)
        if not os.path.exists(filepath):
            files.write_file(filepath, initial_content)


def get_knowledge_files_count(name: str):
    knowledge_folder = files.get_abs_path(
        get_project_meta_folder(name, PROJECT_KNOWLEDGE_DIR)
    )
    if not os.path.exists(knowledge_folder):
        return 0
    return len(files.list_files_in_dir_recursively(knowledge_folder))

def get_file_structure(name: str, basic_data: BasicProjectData|None=None) -> str:
    project_folder = get_project_folder(name)
    if basic_data is None:
        basic_data = load_basic_project_data(name)
    
    try:
        # file_tree expects a path relative to the app root
        rel_folder = files.deabsolute_path(project_folder)
        tree = str(file_tree.file_tree(
            rel_folder,
            max_depth=basic_data["file_structure"]["max_depth"],
            max_files=basic_data["file_structure"]["max_files"],
            max_folders=basic_data["file_structure"]["max_folders"],
            max_lines=basic_data["file_structure"]["max_lines"],
            ignore=basic_data["file_structure"]["gitignore"],
            output_mode=file_tree.OUTPUT_MODE_STRING
        ))
    except Exception as e:
        PrintStyle.error(f"Error rendering file tree for project '{name}': {e}")
        tree = f"# Error rendering file tree: {e}"

    # empty?
    if "\n" not in tree:
        tree += "\n # Empty"

    return tree


# ── Decomposition Completeness Check ────────────────────────────────
# Migrated from gate_quality.py (which is now deleted).
# Natural home: this module already owns get_decomp_index_path().

def is_implementation_phase(phase_seq: str) -> bool:
    """Check if a phase sequence represents an implementation phase.

    Implementation phases:
      - Semver (3+ segments): major >= 1 (e.g., "1.0.0", "3.2.1")
      - Simple (1-2 segments): major >= 3 (e.g., "3", "3.5", "4")
    """
    parts = str(phase_seq).split(".")
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return False
    if len(parts) >= 3:
        return major >= 1
    return major >= 3


def check_decomposition_completeness(
    project_dir: str,
    planning_only: bool = False,
) -> dict:
    """Check if all decomposition phases are completed.

    Reads decomposition_index.json and returns phase completion status.

    Args:
        project_dir: Absolute path to the project directory.
        planning_only: If True, skip implementation phases.

    Returns:
        Dict with: all_complete, total, completed, pending,
                   pending_phases, current_phase
    """
    decomp_path = get_decomp_index_path(project_dir)

    _EMPTY = {
        "all_complete": True,
        "total": 0,
        "completed": 0,
        "pending": 0,
        "pending_phases": [],
        "current_phase": None,
    }

    if not os.path.isfile(decomp_path):
        return _EMPTY

    try:
        with open(decomp_path, "r", encoding="utf-8") as f:
            decomp = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return _EMPTY

    # Normalize dict format to list
    if isinstance(decomp, dict):
        decomp = (
            decomp.get("tasks")
            or decomp.get("milestones")
            or decomp.get("phases")
            or []
        )
        if isinstance(decomp, list):
            for item in decomp:
                if isinstance(item, dict) and "seq" not in item and "id" in item:
                    item["seq"] = item["id"]

    if not isinstance(decomp, list):
        return _EMPTY

    # Auto-reconcile phase statuses from deliverable files
    try:
        from python.tools.requirements import _reconcile_decomp_statuses
        _reconcile_decomp_statuses(decomp, project_dir)
    except Exception:
        pass

    # Filter implementation phases in planning-only mode
    if planning_only:
        decomp = [p for p in decomp if not is_implementation_phase(p.get("seq", "?"))]

    from python.helpers.status_constants import PHASE_DONE_STATUSES as _DONE_STATUSES
    from python.helpers.phase_parser import parse_phase_seq

    total = len(decomp)
    completed = 0
    pending_phases = []
    highest_completed_seq = None

    for phase in decomp:
        status = str(phase.get("status", "pending")).lower().strip()
        if status in _DONE_STATUSES:
            completed += 1
            try:
                seq_val = parse_phase_seq(phase.get("seq", "0"))
                if highest_completed_seq is None or seq_val > highest_completed_seq:
                    highest_completed_seq = seq_val
            except Exception:
                pass
        else:
            pending_phases.append({
                "seq": phase.get("seq", "?"),
                "title": phase.get("title", "Untitled"),
                "status": status,
            })

    pending = total - completed

    # Forward-only: current_phase must never go backwards
    if pending_phases and highest_completed_seq is not None:
        forward_pending = []
        for pp in pending_phases:
            try:
                pp_seq = parse_phase_seq(pp["seq"])
                if pp_seq > highest_completed_seq:
                    forward_pending.append(pp)
            except Exception:
                forward_pending.append(pp)
        current_phase = forward_pending[0]["seq"] if forward_pending else None
    else:
        current_phase = pending_phases[0]["seq"] if pending_phases else None

    return {
        "all_complete": pending == 0,
        "total": total,
        "completed": completed,
        "pending": pending,
        "pending_phases": pending_phases,
        "current_phase": current_phase,
    }

