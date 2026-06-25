from __future__ import annotations
"""
Project Setup Helper for AGIX

Implements the mise-en-place stage for MultiAgentDev workflow.
Orchestrates project creation, git initialization, MISE setup, and environment preparation.
"""

import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python.helpers import files, projects
from python.helpers.mise_manager import Framework, MiseManager, get_mise_manager

logger = logging.getLogger("agix.project_setup")


def is_running_in_docker() -> bool:
    """
    Detect if we're running inside a Docker container.
    
    Returns:
        True if running in Docker, False otherwise
    """
    # Check for /.dockerenv file (most reliable)
    if os.path.exists("/.dockerenv"):
        return True
    
    # Check for /agix directory (AGIX container mount point)
    if os.path.exists("/agix") and os.path.isdir("/agix"):
        return True
    
    # Check for /agix directory (legacy mount point)
    if os.path.exists("/agix") and os.path.isdir("/agix"):
        return True
    
    # Check cgroup for docker/container indicators
    try:
        with open("/proc/1/cgroup", "r") as f:
            content = f.read()
            if "docker" in content or "containerd" in content or "lxc" in content:
                return True
    except (FileNotFoundError, PermissionError):
        pass
    
    return False


def get_projects_base_path() -> str:
    """
    Get the correct projects base path based on execution environment.
    
    In Docker: /agix/usr/projects
    Locally: <agix-root>/usr/projects
    
    Returns:
        The absolute path to the projects directory
    """
    if is_running_in_docker():
        if os.path.exists("/agix/usr/projects"):
            return "/agix/usr/projects"
        return "/agix/usr/projects"
    else:
        return files.get_abs_path("usr/projects")


class SetupStep(Enum):
    """Steps in the mise-en-place process."""
    CREATE_DIRECTORY = "create_directory"
    INIT_GIT = "init_git"
    DETECT_FRAMEWORK = "detect_framework"
    CREATE_MISE_CONFIG = "create_mise_config"
    CREATE_GITIGNORE = "create_gitignore"
    CREATE_README = "create_readme"
    INIT_AGIX_PROJECT = "init_agix_project"
    INSTALL_TOOLS = "install_tools"
    GENERATE_DIGEST = "generate_digest"
    VERIFY_ENVIRONMENT = "verify_environment"


@dataclass
class SetupResult:
    """Result of a setup step."""
    step: SetupStep
    success: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MiseEnPlaceResult:
    """Complete result of mise-en-place process."""
    project_name: str
    project_path: str
    framework: Framework
    success: bool
    steps: List[SetupResult] = field(default_factory=list)
    error: Optional[str] = None
    duration_seconds: float = 0.0
    
    @property
    def completed_steps(self) -> List[SetupStep]:
        """Get list of successfully completed steps."""
        return [s.step for s in self.steps if s.success]
    
    @property
    def failed_steps(self) -> List[SetupStep]:
        """Get list of failed steps."""
        return [s.step for s in self.steps if not s.success]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "project_name": self.project_name,
            "project_path": self.project_path,
            "framework": self.framework.value,
            "success": self.success,
            "steps": [
                {
                    "step": s.step.value,
                    "success": s.success,
                    "message": s.message,
                    "details": s.details,
                }
                for s in self.steps
            ],
            "error": self.error,
            "duration_seconds": self.duration_seconds,
        }


class ProjectSetup:
    """
    Orchestrates the mise-en-place stage for new projects.
    
    The mise-en-place process:
    1. Create project directory at /projects/<name>
    2. Initialize git repository
    3. Detect framework/language
    4. Create .mise.toml configuration
    5. Create .gitignore
    6. Create README.md
    7. Initialize AGIX project metadata
    8. Install MISE tools (optional)
    9. Verify environment is ready
    """
    
    # Default projects directory (relative to AGIX root)
    PROJECTS_DIR = "usr/projects"
    
    def __init__(
        self,
        project_name: str,
        description: str = "",
        framework: Optional[Framework] = None,
        auto_install_tools: bool = False,
        project_data: Optional[projects.BasicProjectData] = None,
    ):
        """
        Initialize project setup.
        
        Args:
            project_name: Name of the project (will be sanitized)
            description: Project description
            framework: Framework to use (auto-detected if None)
            auto_install_tools: Whether to automatically install MISE tools
            project_data: Full project metadata (BasicProjectData)
        """
        self.project_name = self._sanitize_name(project_name)
        self.description = description
        self.framework = framework
        self.auto_install_tools = auto_install_tools
        self.project_data = project_data
        
        # Paths
        self.project_path = self._get_project_path()
        self.mise_manager: Optional[MiseManager] = None
        
        # Results tracking
        self._results: List[SetupResult] = []
    
    @staticmethod
    def _sanitize_name(name: str) -> str:
        """
        Sanitize project name for filesystem use.
        
        Args:
            name: Raw project name
            
        Returns:
            Sanitized name safe for filesystem
        """
        # Replace spaces with hyphens
        name = name.replace(" ", "-")
        
        # Remove or replace invalid characters
        valid_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        name = "".join(c if c in valid_chars else "-" for c in name)
        
        # Remove consecutive hyphens
        while "--" in name:
            name = name.replace("--", "-")
        
        # Remove leading/trailing hyphens
        name = name.strip("-")
        
        # Ensure not empty
        if not name:
            name = "project"
        
        # Lowercase
        return name.lower()
    
    def _get_project_path(self) -> Path:
        """Get the full project path, container-aware."""
        base_path = get_projects_base_path()
        return Path(base_path) / self.project_name
    
    def _add_result(
        self,
        step: SetupStep,
        success: bool,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> SetupResult:
        """Add a step result."""
        result = SetupResult(
            step=step,
            success=success,
            message=message,
            details=details or {},
        )
        self._results.append(result)
        
        if success:
            logger.info(f"[{step.value}] {message}")
        else:
            logger.error(f"[{step.value}] {message}")
        
        return result
    
    def _create_directory(self) -> SetupResult:
        """Create the project directory."""
        try:
            if self.project_path.exists():
                # Log as info since this is a valid state for idempotency
                logger.info(f"[create_directory] Project directory already exists: {self.project_path}")
                return self._add_result(
                    SetupStep.CREATE_DIRECTORY,
                    True,
                    f"Project directory already exists: {self.project_path}",
                    {"existed": True},
                )
            
            self.project_path.mkdir(parents=True, exist_ok=True)
            return self._add_result(
                SetupStep.CREATE_DIRECTORY,
                True,
                f"Created project directory: {self.project_path}",
                {"created": True},
            )
        except Exception as e:
            return self._add_result(
                SetupStep.CREATE_DIRECTORY,
                False,
                f"Failed to create directory: {e}",
            )
    
    def _init_git(self) -> SetupResult:
        """Initialize git repository."""
        git_dir = self.project_path / ".git"
        
        if git_dir.exists():
            # Log as info since this is a valid state for idempotency
            logger.info("[init_git] Git repository already initialized")
            return self._add_result(
                SetupStep.INIT_GIT,
                True,
                "Git repository already initialized",
                {"existed": True},
            )
        
        try:
            result = subprocess.run(
                ["git", "init"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            if result.returncode == 0:
                # Set initial branch to main
                subprocess.run(
                    ["git", "branch", "-M", "main"],
                    cwd=self.project_path,
                    capture_output=True,
                    timeout=10,
                )
                
                return self._add_result(
                    SetupStep.INIT_GIT,
                    True,
                    "Initialized git repository",
                    {"branch": "main"},
                )
            else:
                return self._add_result(
                    SetupStep.INIT_GIT,
                    False,
                    f"Git init failed: {result.stderr}",
                )
        except FileNotFoundError:
            return self._add_result(
                SetupStep.INIT_GIT,
                False,
                "Git not found in PATH",
            )
        except Exception as e:
            return self._add_result(
                SetupStep.INIT_GIT,
                False,
                f"Git init error: {e}",
            )
    
    def _detect_framework(self) -> SetupResult:
        """Detect project framework."""
        self.mise_manager = get_mise_manager(str(self.project_path))
        
        if self.framework:
            return self._add_result(
                SetupStep.DETECT_FRAMEWORK,
                True,
                f"Using specified framework: {self.framework.value}",
                {"framework": self.framework.value, "specified": True},
            )
        
        detection = self.mise_manager.detect_framework()
        self.framework = detection.primary
        
        return self._add_result(
            SetupStep.DETECT_FRAMEWORK,
            True,
            f"Detected framework: {self.framework.value} (confidence: {detection.confidence:.2f})",
            {
                "framework": self.framework.value,
                "confidence": detection.confidence,
                "detected_files": detection.detected_files,
                "secondary": detection.secondary.value if detection.secondary else None,
            },
        )
    
    def _create_mise_config(self) -> SetupResult:
        """Create .mise.toml configuration."""
        if not self.mise_manager:
            self.mise_manager = get_mise_manager(str(self.project_path))
        
        success, message = self.mise_manager.write_mise_toml(
            framework=self.framework,
            overwrite=False,
        )
        
        return self._add_result(
            SetupStep.CREATE_MISE_CONFIG,
            success,
            message,
            {"framework": self.framework.value if self.framework else "generic"},
        )
    
    def _create_gitignore(self) -> SetupResult:
        """Create .gitignore file."""
        if not self.mise_manager:
            self.mise_manager = get_mise_manager(str(self.project_path))
        
        success, message = self.mise_manager.write_gitignore(
            framework=self.framework,
            overwrite=False,
        )
        
        return self._add_result(
            SetupStep.CREATE_GITIGNORE,
            success,
            message,
        )
    
    def _create_readme(self) -> SetupResult:
        """Create README.md file."""
        readme_path = self.project_path / "README.md"
        
        if readme_path.exists():
            # Log as info since this is a valid state for idempotency
            logger.info("[create_readme] README.md already exists")
            return self._add_result(
                SetupStep.CREATE_README,
                True,
                "README.md already exists",
                {"existed": True},
            )
        
        try:
            framework_name = self.framework.value if self.framework else "Generic"
            content = f"""# {self.project_name}

{self.description or 'A new project created with AGIX.'}

## Project Type

{framework_name.title()} project

## Getting Started

### Prerequisites

This project uses [MISE](https://mise.jdx.dev/) for environment management.

```bash
# Install MISE (if not already installed)
curl https://mise.run | sh

# Install project tools
mise install

# Trust this directory
mise trust
```

### Development

```bash
# Install dependencies
mise run install

# Run tests
mise run test

# Start development
mise run dev
```

## Project Structure

```
{self.project_name}/
├── .mise.toml      # MISE configuration
├── .gitignore      # Git ignore rules
├── {projects.PROJECT_META_DIR}/  # AGIX project metadata
│   ├── project.json
│   ├── instructions/
│   └── knowledge/
└── README.md       # This file
```

## Created

Created on {datetime.now().strftime('%Y-%m-%d')} with AGIX - MultiAgentDev.
"""
            readme_path.write_text(content)
            return self._add_result(
                SetupStep.CREATE_README,
                True,
                "Created README.md",
            )
        except Exception as e:
            return self._add_result(
                SetupStep.CREATE_README,
                False,
                f"Failed to create README.md: {e}",
            )
    
    def _init_agix_project(self) -> SetupResult:
        """Initialize AGIX project metadata."""
        try:
            # Check if metadata directory already exists
                        # Check if metadata directory already exists
            meta_path = Path(projects.get_project_meta_folder(str(self.project_path)))
            if meta_path.exists():
                # Log as info since this is a valid state for idempotency

                # Log as info since this is a valid state for idempotency
                logger.info(f"[init_agix_project] AGIX project metadata already exists")
                return self._add_result(
                    SetupStep.INIT_AGIX_PROJECT,
                    True,
                    "AGIX project metadata already exists",
                    {"existed": True},
                )
            
            # Create project metadata directory structure directly in project path
            # If meta_path doesn't exist, we use the default PROJECT_META_DIR for new metadata
            if not meta_path.exists():
                meta_path = self.project_path / projects.PROJECT_META_DIR
            
            meta_path.mkdir(parents=True, exist_ok=True)
            (meta_path / projects.PROJECT_INSTRUCTIONS_DIR).mkdir(exist_ok=True)
            (meta_path / projects.PROJECT_KNOWLEDGE_DIR).mkdir(exist_ok=True)
            
            # Create project data
            project_title = self.project_name.replace("-", " ").title()
            
            if self.project_data:
                # use provided data, but ensure title is set if missing
                data = self.project_data.copy()
                if not data.get("title"): data["title"] = project_title
                if not data.get("description"): data["description"] = self.description or f"A {self.framework.value if self.framework else 'generic'} project"
            else:
                # create default data
                data = projects.BasicProjectData(
                    title=project_title,
                    description=self.description or f"A {self.framework.value if self.framework else 'generic'} project",
                    instructions="",
                    color="",
                    memory="own",
                    file_structure=projects._default_file_structure_settings(),
                )
            
            # Use projects helper to create/save (which also handles normalization)
            # We don't call projects.create_project here because we already created the folder
            # and we are inside the ProjectSetup flow. We just want to save the header.
            projects.save_project_header(self.project_name, data)
            
            return self._add_result(
                SetupStep.INIT_AGIX_PROJECT,
                True,
                "Initialized AGIX project metadata",
                {"title": data["title"]},
            )
        except Exception as e:
            return self._add_result(
                SetupStep.INIT_AGIX_PROJECT,
                False,
                f"Failed to initialize AGIX project: {e}",
            )
    
    def _install_tools(self) -> SetupResult:
        """Install MISE tools."""
        if not self.auto_install_tools:
            return self._add_result(
                SetupStep.INSTALL_TOOLS,
                True,
                "Tool installation skipped (auto_install_tools=False)",
                {"skipped": True},
            )
        
        # Check if MISE is available
        available, version_or_error = MiseManager.verify_mise_installed()
        if not available:
            return self._add_result(
                SetupStep.INSTALL_TOOLS,
                False,
                f"MISE not available: {version_or_error}",
            )
        
        if not self.mise_manager:
            self.mise_manager = get_mise_manager(str(self.project_path))
        
        # Trust directory first
        self.mise_manager.trust_directory()
        
        # Install tools
        success, message = self.mise_manager.install_tools()
        
        return self._add_result(
            SetupStep.INSTALL_TOOLS,
            success,
            message,
        )
    
    def _generate_digest(self) -> SetupResult:
        """
        Generate a codebase digest using gitingest.
        Enforces a 125,000 word limit for context window optimization.
        """
        import shutil
        import os
        
        gitingest_bin = shutil.which("gitingest") or "/opt/homebrew/bin/gitingest"
        if not os.path.exists(gitingest_bin):
            return self._add_result(
                SetupStep.GENERATE_DIGEST,
                True, # Skip silently if tool missing, not a fatal setup error
                "Gitingest not found, skipping codebase digest generation.",
                {"skipped": True}
            )

        try:
            # We use the same excludes as repo automation
            excludes = [
                ".git", "node_modules", "vendor", "bower_components", "venv", ".venv", 
                "__pycache__", "dist", "build", "tmp", "temp", "out", "target", "vendor",
                "*.min.js", "*.min.css", "*.map", "*.log", "*.bin", "*.exe", "*.so", "*.o", "*.json", "*.md",
                "*.pyc", "*.pyo", "*.pyd", "*.db", "*.sqlite", "*.wasm", "*.dll", "*.dylib", "*.lib"
            ]
            
            cmd = [gitingest_bin, str(self.project_path)]
            for pat in excludes:
                cmd.extend(["-e", pat])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            
            if result.returncode == 0:
                content = result.stdout
                # Enforce Word Limit
                words = content.split()
                max_words = 125000
                if len(words) > max_words:
                    content = " ".join(words[:max_words]) + "\n\n... [TRUNCATED DUE TO 125K WORD LIMIT] ..."
                
                digest_path = self.project_path / "digest.txt"
                digest_path.write_text(content)
                
                return self._add_result(
                    SetupStep.GENERATE_DIGEST,
                    True,
                    f"Generated codebase digest ({len(words)} words) at digest.txt",
                )
            else:
                return self._add_result(
                    SetupStep.GENERATE_DIGEST,
                    True, # Non-fatal
                    f"Gitingest failed: {result.stderr}",
                )
        except Exception as e:
            return self._add_result(
                SetupStep.GENERATE_DIGEST,
                True, # Non-fatal
                f"Error generating digest: {e}",
            )

    def _verify_environment(self) -> SetupResult:
        """Verify the environment is ready."""
        checks = {
            "directory_exists": self.project_path.exists(),
            "git_initialized": (self.project_path / ".git").exists(),
            "mise_config": (self.project_path / ".mise.toml").exists(),
            "gitignore": (self.project_path / ".gitignore").exists(),
            "readme": (self.project_path / "README.md").exists(),
            "agix_project": Path(projects.get_project_meta_folder(str(self.project_path))).exists(),
        }
        
        all_passed = all(checks.values())
        failed_checks = [k for k, v in checks.items() if not v]
        
        if all_passed:
            return self._add_result(
                SetupStep.VERIFY_ENVIRONMENT,
                True,
                "Environment verification passed",
                {"checks": checks},
            )
        else:
            return self._add_result(
                SetupStep.VERIFY_ENVIRONMENT,
                False,
                f"Environment verification failed: {failed_checks}",
                {"checks": checks, "failed": failed_checks},
            )
    
    def run(self) -> MiseEnPlaceResult:
        """
        Execute the complete mise-en-place process.
        
        Returns:
            MiseEnPlaceResult with all step results
        """
        start_time = datetime.now()
        self._results = []
        
        logger.info(f"Starting mise-en-place for project: {self.project_name}")
        
        # Execute steps in order
        steps = [
            self._create_directory,
            self._init_git,
            self._detect_framework,
            self._create_mise_config,
            self._create_gitignore,
            self._create_readme,
            self._init_agix_project,
            self._install_tools,
            self._generate_digest,
            self._verify_environment,
        ]
        
        error = None
        for step_fn in steps:
            try:
                result = step_fn()
                # Continue even if non-critical steps fail
                if not result.success and result.step in [
                    SetupStep.CREATE_DIRECTORY,
                    SetupStep.VERIFY_ENVIRONMENT,
                ]:
                    error = result.message
                    break
            except Exception as e:
                error = f"Unexpected error in {step_fn.__name__}: {e}"
                logger.exception(error)
                break
        
        duration = (datetime.now() - start_time).total_seconds()
        
        # Determine overall success
        critical_steps = [
            SetupStep.CREATE_DIRECTORY,
            SetupStep.DETECT_FRAMEWORK,
            SetupStep.INIT_AGIX_PROJECT,
        ]
        success = all(
            any(r.step == step and r.success for r in self._results)
            for step in critical_steps
        )
        
        result = MiseEnPlaceResult(
            project_name=self.project_name,
            project_path=str(self.project_path),
            framework=self.framework or Framework.GENERIC,
            success=success and error is None,
            steps=self._results,
            error=error,
            duration_seconds=duration,
        )
        
        if result.success:
            logger.info(f"Mise-en-place completed successfully for {self.project_name}")
        else:
            logger.error(f"Mise-en-place failed for {self.project_name}: {error}")
        
        return result


def mise_en_place(
    project_name: str,
    description: str = "",
    framework: Optional[str] = None,
    auto_install_tools: bool = False,
    project_data: Optional[projects.BasicProjectData] = None,
) -> MiseEnPlaceResult:
    """
    Execute mise-en-place for a new project.
    
    This is the main entry point for project setup.
    
    Args:
        project_name: Name of the project
        description: Project description
        framework: Framework name (python, nodejs, rust, go, ruby, java, fullstack, generic)
        auto_install_tools: Whether to automatically install MISE tools
        project_data: Full project metadata
        
    Returns:
        MiseEnPlaceResult with setup results
    """
    # Convert framework string to enum
    framework_enum = None
    if framework:
        try:
            framework_enum = Framework(framework.lower())
        except ValueError:
            logger.warning(f"Unknown framework '{framework}', will auto-detect")
    
    setup = ProjectSetup(
        project_name=project_name,
        description=description,
        framework=framework_enum,
        auto_install_tools=auto_install_tools,
        project_data=project_data,
    )
    
    return setup.run()


def ensure_project_directory(project_name: str) -> Tuple[bool, str]:
    """
    Ensure a project directory exists at /projects/<name>.
    
    Args:
        project_name: Name of the project
        
    Returns:
        Tuple of (success, path_or_error)
    """
    sanitized = ProjectSetup._sanitize_name(project_name)
    project_path = Path(files.get_abs_path(ProjectSetup.PROJECTS_DIR, sanitized))
    
    try:
        project_path.mkdir(parents=True, exist_ok=True)
        return True, str(project_path)
    except Exception as e:
        return False, str(e)


def validate_project_ready(project_path: str) -> Dict[str, bool]:
    """
    Validate that a project is ready for development and has complete metadata.
    
    Args:
        project_path: Path to the project
        
    Returns:
        Dictionary of check name to pass/fail
    """
    path = Path(project_path)
    
    checks = {
        "directory_exists": path.exists(),
        "git_initialized": (path / ".git").exists(),
        "mise_config": (path / ".mise.toml").exists(),
        "gitignore": (path / ".gitignore").exists(),
        "readme": (path / "README.md").exists(),
        "project_meta": (path / ".agix.proj").exists() or (path / ".agix.proj").exists(),
    }

    # Check metadata completeness in whichever meta dir exists
    meta_dir_name = ".agix.proj" if (path / ".agix.proj").exists() else ".agix.proj"
    if checks["project_meta"]:
        header_path = path / meta_dir_name / "project.json"
        if header_path.exists():
            try:
                import json
                data = json.loads(header_path.read_text())
                # Check for mandatory fields
                checks["metadata_complete"] = bool(
                    data.get("title") and 
                    data.get("description") and 
                    len(data.get("description", "")) > 20 # Require some substance
                )
            except Exception:
                checks["metadata_complete"] = False
        else:
            checks["metadata_complete"] = False
    else:
        checks["metadata_complete"] = False
        
    return checks


def get_project_framework(project_path: str) -> Framework:
    """
    Get the detected framework for a project.
    
    Args:
        project_path: Path to the project
        
    Returns:
        Detected Framework
    """
    mise = get_mise_manager(project_path)
    detection = mise.detect_framework()
    return detection.primary
