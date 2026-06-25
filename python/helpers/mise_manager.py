from __future__ import annotations
"""
MISE Manager for AGIX

Manages MISE (polyglot runtime manager) integration for project environments.
Handles framework detection, .mise.toml generation, and environment activation.
"""

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from python.helpers.project_layout_detector import FRAMEWORK_NAMES, detect_layout

logger = logging.getLogger("agix.mise_manager")


class Framework(Enum):
    """Supported project frameworks/languages."""
    PYTHON = "python"
    NODEJS = "nodejs"
    RUST = "rust"
    GO = "go"
    RUBY = "ruby"
    JAVA = "java"
    FULLSTACK = "fullstack"  # Python + Node.js
    GENERIC = "generic"


@dataclass
class FrameworkDetection:
    """Result of framework detection."""
    primary: Framework
    secondary: Optional[Framework] = None
    confidence: float = 0.0
    detected_files: Optional[List[str]] = None
    
    def __post_init__(self):
        if self.detected_files is None:
            self.detected_files = []


# Framework detection patterns
FRAMEWORK_PATTERNS: Dict[Framework, Dict[str, List[str]]] = {
    Framework.PYTHON: {
        "files": ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"],
        "extensions": [".py"],
        "dirs": ["venv", ".venv", "__pycache__"],
    },
    Framework.NODEJS: {
        "files": ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
        "extensions": [".js", ".ts", ".jsx", ".tsx", ".mjs"],
        "dirs": ["node_modules"],
    },
    Framework.RUST: {
        "files": ["Cargo.toml", "Cargo.lock"],
        "extensions": [".rs"],
        "dirs": ["target"],
    },
    Framework.GO: {
        "files": ["go.mod", "go.sum"],
        "extensions": [".go"],
        "dirs": [],
    },
    Framework.RUBY: {
        "files": ["Gemfile", "Gemfile.lock", "Rakefile"],
        "extensions": [".rb", ".erb"],
        "dirs": [".bundle"],
    },
    Framework.JAVA: {
        "files": ["pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle"],
        "extensions": [".java", ".kt", ".scala"],
        "dirs": ["target", "build"],
    },
}


# ── ProjectLayout.framework → Framework enum mapping ──
# Maps ALL known framework identifiers from detect_layout() to this module's
# Framework enum. This is the bridge between the canonical detector and MISE.
_LAYOUT_TO_MISE_FRAMEWORK: Dict[str, Framework] = {
    # Web frontends → NODEJS
    "nextjs-app": Framework.NODEJS,
    "nextjs-pages": Framework.NODEJS,
    "vite-react": Framework.NODEJS,
    "vite-vue": Framework.NODEJS,
    "vite-svelte": Framework.NODEJS,
    "nuxt": Framework.NODEJS,
    "sveltekit": Framework.NODEJS,
    "astro": Framework.NODEJS,
    "remix": Framework.NODEJS,
    "angular": Framework.NODEJS,
    "react-native": Framework.NODEJS,
    "static-html": Framework.GENERIC,
    # Backend
    "flask": Framework.PYTHON,
    "django": Framework.PYTHON,
    "fastapi": Framework.PYTHON,
    "python": Framework.PYTHON,
    "go": Framework.GO,
    "rust": Framework.RUST,
    "rails": Framework.RUBY,
    "sinatra": Framework.RUBY,
    # Mobile
    "flutter": Framework.GENERIC,
    "android": Framework.JAVA,
    "swift-ios": Framework.GENERIC,
    "swift-package": Framework.GENERIC,
    # Fallback
    "unknown": Framework.GENERIC,
}


# MISE template configurations
MISE_TEMPLATES: Dict[Framework, str] = {
    Framework.PYTHON: '''# MISE configuration for Python project
[tools]
python = "3.11"
ripgrep = "latest"
ast-grep = "latest"

[env]
PYTHONPATH = "."
PYTHONDONTWRITEBYTECODE = "1"

[tasks.install]
run = "pip install -r requirements.txt"

[tasks.test]
run = "pytest -v"

[tasks.lint]
run = "ruff check ."

[tasks.format]
run = "ruff format ."

[tasks.dev]
run = "python main.py"
''',
    
    Framework.NODEJS: '''# MISE configuration for Node.js project
[tools]
node = "20"
ripgrep = "latest"
ast-grep = "latest"

[tasks.install]
run = "npm install"

[tasks.test]
run = "npm test"

[tasks.lint]
run = "npm run lint"

[tasks.dev]
run = "NODE_ENV=development npm run dev"

[tasks.build]
run = "NODE_ENV=production npm run build"
''',
    
    Framework.RUST: '''# MISE configuration for Rust project
[tools]
rust = "stable"
ripgrep = "latest"
ast-grep = "latest"

[tasks.build]
run = "cargo build"

[tasks.test]
run = "cargo test"

[tasks.run]
run = "cargo run"

[tasks.release]
run = "cargo build --release"

[tasks.lint]
run = "cargo clippy"
''',
    
    Framework.GO: '''# MISE configuration for Go project
[tools]
go = "1.21"
ripgrep = "latest"
ast-grep = "latest"

[tasks.build]
run = "go build -o bin/app ."

[tasks.test]
run = "go test ./..."

[tasks.run]
run = "go run ."

[tasks.lint]
run = "golangci-lint run"
''',
    
    Framework.RUBY: '''# MISE configuration for Ruby project
[tools]
ruby = "3.2"
ripgrep = "latest"
ast-grep = "latest"

[tasks.install]
run = "bundle install"

[tasks.test]
run = "bundle exec rspec"

[tasks.lint]
run = "bundle exec rubocop"
''',
    
    Framework.JAVA: '''# MISE configuration for Java project
[tools]
java = "21"
maven = "3.9"
ripgrep = "latest"
ast-grep = "latest"

[tasks.build]
run = "mvn compile"

[tasks.test]
run = "mvn test"

[tasks.package]
run = "mvn package"

[tasks.run]
run = "mvn exec:java"
''',
    
    Framework.FULLSTACK: '''# MISE configuration for Full-Stack project (Python + Node.js)
[tools]
python = "3.11"
node = "20"
ripgrep = "latest"
ast-grep = "latest"

[env]
PYTHONPATH = "backend"

[tasks.install]
run = """
cd backend && pip install -r requirements.txt
cd ../frontend && npm install
"""

[tasks.test]
run = """
cd backend && pytest
cd ../frontend && npm test
"""

[tasks.dev-backend]
run = "cd backend && python main.py"

[tasks.dev-frontend]
run = "cd frontend && NODE_ENV=development npm run dev"

[tasks.build]
run = "cd frontend && NODE_ENV=production npm run build"
''',
    
    Framework.GENERIC: '''# MISE configuration
[tools]
# Add your tools here, e.g.:
# python = "3.11"
# node = "20"
ripgrep = "latest"
ast-grep = "latest"

[env]
# Add environment variables here

[tasks.test]
run = "echo 'No tests configured'"
''',
}

# AGIX-specific gitignore entries — appended to EVERY framework template
# to prevent agent metadata from leaking into customer repos (Issue #1092)
AGIX_GITIGNORE_BLOCK = '''
# AGIX agent metadata (NEVER commit)
.agix.proj/
.agix.proj/
'''

# Gitignore templates per framework
GITIGNORE_TEMPLATES: Dict[Framework, str] = {
    Framework.PYTHON: '''# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
venv/
.venv/
ENV/

# IDE
.idea/
.vscode/
*.swp
*.swo

# Testing
.pytest_cache/
.coverage
htmlcov/

# MISE
.mise.local.toml

# Environment
.env
.env.local
''',
    
    Framework.NODEJS: '''# Node.js
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*

# Build
dist/
build/
.next/
out/

# IDE
.idea/
.vscode/
*.swp
*.swo

# Testing
coverage/
.nyc_output/

# MISE
.mise.local.toml

# Environment
.env
.env.local
.env.*.local
''',
    
    Framework.RUST: '''# Rust
/target/
Cargo.lock

# IDE
.idea/
.vscode/
*.swp
*.swo

# MISE
.mise.local.toml
''',
    
    Framework.GO: '''# Go
/bin/
/vendor/
*.exe
*.exe~
*.dll
*.so
*.dylib
*.test
*.out

# IDE
.idea/
.vscode/
*.swp
*.swo

# MISE
.mise.local.toml
''',
    
    Framework.RUBY: '''# Ruby
*.gem
*.rbc
/.config
/coverage/
/InstalledFiles
/pkg/
/spec/reports/
/spec/examples.txt
/test/tmp/
/test/version_tmp/
/tmp/

# Bundler
/.bundle/
/vendor/bundle
/lib/bundler/man/

# IDE
.idea/
.vscode/
*.swp
*.swo

# MISE
.mise.local.toml

# Environment
.env
''',
    
    Framework.JAVA: '''# Java
*.class
*.log
*.jar
*.war
*.nar
*.ear
*.zip
*.tar.gz
*.rar

# Maven
target/
pom.xml.tag
pom.xml.releaseBackup
pom.xml.versionsBackup
pom.xml.next
release.properties

# Gradle
.gradle/
build/

# IDE
.idea/
*.iml
.vscode/
*.swp
*.swo

# MISE
.mise.local.toml
''',
    
    Framework.FULLSTACK: '''# Python
__pycache__/
*.py[cod]
venv/
.venv/
.pytest_cache/

# Node.js
node_modules/
dist/
build/

# IDE
.idea/
.vscode/
*.swp
*.swo

# Testing
coverage/
htmlcov/

# MISE
.mise.local.toml

# Environment
.env
.env.local
''',
    
    Framework.GENERIC: '''# IDE
.idea/
.vscode/
*.swp
*.swo

# MISE
.mise.local.toml

# Environment
.env
.env.local
''',
}

# Append AGIX exclusions to every template (Issue #1092)
for _fw in list(GITIGNORE_TEMPLATES.keys()):
    GITIGNORE_TEMPLATES[_fw] = GITIGNORE_TEMPLATES[_fw].rstrip() + AGIX_GITIGNORE_BLOCK


class MiseManager:
    """
    Manages MISE environments for AGIX projects.
    
    Handles:
    - Framework detection from project files
    - .mise.toml generation
    - MISE installation verification
    - Environment activation
    - Tool installation
    """
    
    def __init__(self, project_path: str):
        """
        Initialize MISE manager for a project.
        
        Args:
            project_path: Path to the project directory
        """
        self.project_path = Path(project_path)
        self._mise_available: Optional[bool] = None
    
    @staticmethod
    def verify_mise_installed() -> Tuple[bool, str]:
        """
        Check if MISE is installed and available.
        
        Returns:
            Tuple of (is_available, version_or_error)
        """
        try:
            result = subprocess.run(
                ["mise", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                logger.info(f"MISE available: {version}")
                return True, version
            else:
                return False, result.stderr.strip()
        except FileNotFoundError:
            return False, "MISE not found in PATH"
        except subprocess.TimeoutExpired:
            return False, "MISE command timed out"
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def install_mise() -> Tuple[bool, str]:
        """
        Attempt to install MISE.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            # Use the official installer
            result = subprocess.run(
                ["sh", "-c", "curl -fsSL https://mise.run | sh"],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                return True, "MISE installed successfully"
            else:
                return False, f"Installation failed: {result.stderr}"
        except Exception as e:
            return False, f"Installation error: {e}"
    
    def detect_framework(self) -> FrameworkDetection:
        """
        Detect the project framework/language.

        Delegates to project_layout_detector.detect_layout() (single source
        of truth) and maps the result to the MISE Framework enum via
        _LAYOUT_TO_MISE_FRAMEWORK.

        Returns:
            FrameworkDetection with primary and optional secondary framework
        """
        if not self.project_path.exists():
            return FrameworkDetection(
                primary=Framework.GENERIC,
                confidence=0.0
            )

        try:
            layout = detect_layout(str(self.project_path))
        except Exception as e:
            logger.warning(f"Error detecting layout: {e}")
            return FrameworkDetection(primary=Framework.GENERIC, confidence=0.0)

        # Map ProjectLayout.framework → MISE Framework enum
        primary = _LAYOUT_TO_MISE_FRAMEWORK.get(
            layout.framework, Framework.GENERIC
        )

        # Confidence: 0.0 for unknown, 0.8 for known frameworks
        confidence = 0.0 if layout.framework == "unknown" else 0.8

        # Detect fullstack: if language is python but framework is Node-based,
        # or vice versa, it might be fullstack
        secondary = None
        if primary == Framework.PYTHON and layout.config_files:
            has_node = any(f in ("package.json",) for f in layout.config_files)
            if has_node:
                primary = Framework.FULLSTACK
                secondary = Framework.PYTHON

        # Collect detected files from source_dirs as evidence
        detected_files = layout.source_dirs[:5] if layout.source_dirs else []

        return FrameworkDetection(
            primary=primary,
            secondary=secondary,
            confidence=confidence,
            detected_files=detected_files,
        )
    
    def generate_mise_toml(
        self,
        framework: Optional[Framework] = None,
        custom_config: Optional[str] = None
    ) -> str:
        """
        Generate .mise.toml content for the project.
        
        Args:
            framework: Framework to use (auto-detected if None)
            custom_config: Custom configuration to append
            
        Returns:
            Generated .mise.toml content
        """
        if framework is None:
            detection = self.detect_framework()
            framework = detection.primary
        
        template = MISE_TEMPLATES.get(framework, MISE_TEMPLATES[Framework.GENERIC])
        
        if custom_config:
            template += f"\n# Custom configuration\n{custom_config}\n"
        
        return template
    
    def write_mise_toml(
        self,
        framework: Optional[Framework] = None,
        custom_config: Optional[str] = None,
        overwrite: bool = False
    ) -> Tuple[bool, str]:
        """
        Write .mise.toml to the project directory.
        
        Args:
            framework: Framework to use (auto-detected if None)
            custom_config: Custom configuration to append
            overwrite: Whether to overwrite existing file
            
        Returns:
            Tuple of (success, message)
        """
        mise_path = self.project_path / ".mise.toml"
        
        if mise_path.exists() and not overwrite:
            return False, f".mise.toml already exists at {mise_path}"
        
        try:
            content = self.generate_mise_toml(framework, custom_config)
            mise_path.write_text(content)
            logger.info(f"Created .mise.toml at {mise_path}")
            return True, f"Created .mise.toml for {framework.value if framework else 'auto-detected'} project"
        except Exception as e:
            logger.error(f"Failed to write .mise.toml: {e}")
            return False, str(e)
    
    def generate_gitignore(self, framework: Optional[Framework] = None) -> str:
        """
        Generate .gitignore content for the project.
        
        Args:
            framework: Framework to use (auto-detected if None)
            
        Returns:
            Generated .gitignore content
        """
        if framework is None:
            detection = self.detect_framework()
            framework = detection.primary
        
        return GITIGNORE_TEMPLATES.get(framework, GITIGNORE_TEMPLATES[Framework.GENERIC])
    
    def write_gitignore(
        self,
        framework: Optional[Framework] = None,
        overwrite: bool = False
    ) -> Tuple[bool, str]:
        """
        Write .gitignore to the project directory.
        
        Args:
            framework: Framework to use (auto-detected if None)
            overwrite: Whether to overwrite existing file
            
        Returns:
            Tuple of (success, message)
        """
        gitignore_path = self.project_path / ".gitignore"
        
        if gitignore_path.exists() and not overwrite:
            return False, f".gitignore already exists at {gitignore_path}"
        
        try:
            content = self.generate_gitignore(framework)
            gitignore_path.write_text(content)
            logger.info(f"Created .gitignore at {gitignore_path}")
            return True, f"Created .gitignore for {framework.value if framework else 'auto-detected'} project"
        except Exception as e:
            logger.error(f"Failed to write .gitignore: {e}")
            return False, str(e)
    
    def trust_directory(self) -> Tuple[bool, str]:
        """
        Trust the project directory for MISE.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            result = subprocess.run(
                ["mise", "trust"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return True, "Directory trusted"
            else:
                return False, result.stderr.strip()
        except Exception as e:
            return False, str(e)
    
    def install_tools(self) -> Tuple[bool, str]:
        """
        Install tools defined in .mise.toml.
        
        Returns:
            Tuple of (success, message)
        """
        mise_path = self.project_path / ".mise.toml"
        if not mise_path.exists():
            return False, "No .mise.toml found"
        
        try:
            result = subprocess.run(
                ["mise", "install"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes for tool installation
            )
            if result.returncode == 0:
                return True, "Tools installed successfully"
            else:
                return False, result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "Tool installation timed out"
        except Exception as e:
            return False, str(e)
    
    def get_environment(self) -> Dict[str, str]:
        """
        Get environment variables from MISE.
        
        Returns:
            Dictionary of environment variables
        """
        env = {}
        try:
            result = subprocess.run(
                ["mise", "env", "-s", "bash"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if '=' in line and line.startswith('export '):
                        key_value = line[7:]  # Remove 'export '
                        if '=' in key_value:
                            key, value = key_value.split('=', 1)
                            env[key] = value.strip('"').strip("'")
        except Exception as e:
            logger.warning(f"Failed to get MISE environment: {e}")
        
        return env
    
    def run_task(self, task_name: str, timeout: int = 300) -> Tuple[bool, str, str]:
        """
        Run a MISE task.
        
        Args:
            task_name: Name of the task to run
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (success, stdout, stderr)
        """
        try:
            result = subprocess.run(
                ["mise", "run", task_name],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return (
                result.returncode == 0,
                result.stdout,
                result.stderr
            )
        except subprocess.TimeoutExpired:
            return False, "", f"Task '{task_name}' timed out after {timeout}s"
        except Exception as e:
            return False, "", str(e)
    
    def execute_command(
        self,
        command: List[str],
        timeout: int = 300
    ) -> Tuple[bool, str, str]:
        """
        Execute a command within the MISE environment.
        
        Args:
            command: Command and arguments to execute
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (success, stdout, stderr)
        """
        try:
            # Use mise exec to run command in environment
            full_command = ["mise", "exec", "--"] + command
            result = subprocess.run(
                full_command,
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return (
                result.returncode == 0,
                result.stdout,
                result.stderr
            )
        except subprocess.TimeoutExpired:
            return False, "", f"Command timed out after {timeout}s"
        except Exception as e:
            return False, "", str(e)
    
    def get_current_tools(self) -> Dict[str, str]:
        """
        Get currently active tool versions.
        
        Returns:
            Dictionary of tool name to version
        """
        tools = {}
        try:
            result = subprocess.run(
                ["mise", "current"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    parts = line.split()
                    if len(parts) >= 2:
                        tools[parts[0]] = parts[1]
        except Exception as e:
            logger.warning(f"Failed to get current tools: {e}")
        
        return tools


def get_mise_manager(project_path: str) -> MiseManager:
    """
    Factory function to get a MISE manager for a project.
    
    Args:
        project_path: Path to the project directory
        
    Returns:
        MiseManager instance
    """
    return MiseManager(project_path)
