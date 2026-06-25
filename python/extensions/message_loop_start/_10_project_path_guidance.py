from __future__ import annotations
"""
Project Path Guidance Extension

Injects project path guidance into the agent's context at the start of each
message loop iteration. This proactively guides the agent to use the correct
project location before it attempts to create directories.

Container-Aware: Provides different guidance based on Docker vs local execution.
"""

import os
from python.helpers.extension import Extension
from python.helpers import files
from python.agent import LoopData
import logging

logger = logging.getLogger("agix.project_path_guidance")


def is_running_in_docker() -> bool:
    """
    Detect if we're running inside a Docker container.
    """
    if os.path.exists("/.dockerenv"):
        return True
    if os.path.exists("/agix") and os.path.isdir("/agix"):
        return True
    if os.path.exists("/agix") and os.path.isdir("/agix"):
        return True
    try:
        with open("/proc/1/cgroup", "r") as f:
            content = f.read()
            if "docker" in content or "containerd" in content or "lxc" in content:
                return True
    except (FileNotFoundError, PermissionError):
        pass
    return False


class ProjectPathGuidance(Extension):
    """
    Extension that injects project path guidance into the agent's context.
    
    This runs at the start of each message loop iteration to ensure the agent
    always knows the correct location for creating projects.
    """
    
    def __init__(self, agent):
        super().__init__(agent)
        self._in_docker = is_running_in_docker()
        self._guidance_injected = False
        
        if self._in_docker:
            self._projects_path = "/agix/usr/projects" if os.path.exists("/agix/usr/projects") else "/agix/usr/projects"
        else:
            self._projects_path = files.get_abs_path("usr/projects")
        
        logger.info(
            f"ProjectPathGuidance initialized: docker={self._in_docker}, "
            f"projects_path={self._projects_path}"
        )
    
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        """
        Inject project path guidance into the loop data.
        
        This adds context that will be available to the agent during processing.
        """
        if loop_data is None:
            return
        
        # Get message from user_message attribute (LoopData is a class, not dict)
        message = ""
        if hasattr(loop_data, 'user_message') and loop_data.user_message:
            # user_message is a history.Message object
            user_msg = loop_data.user_message
            if hasattr(user_msg, 'content'):
                message = str(user_msg.content) if user_msg.content else ""
            elif isinstance(user_msg, dict):
                message = str(user_msg.get('content', ''))
        
        if not message:
            return
        
        # Check if the message mentions project creation keywords
        project_keywords = [
            "create", "new", "setup", "init", "start", "build",
            "project", "app", "api", "website", "dashboard", "cli",
            "flask", "django", "express", "react", "vue", "rust", "go"
        ]
        
        message_lower = message.lower()
        is_project_related = any(kw in message_lower for kw in project_keywords)
        
        if is_project_related:
            # Inject guidance into extras_persistent
            guidance = self._generate_guidance()
            
            # Add to loop_data extras_persistent (LoopData attribute)
            if hasattr(loop_data, 'extras_persistent'):
                loop_data.extras_persistent["project_path_guidance"] = guidance
            
            logger.debug(f"Injected project path guidance for message: {message[:50]}...")
    
    def _generate_guidance(self) -> str:
        """
        Generate the project path guidance text.
        """
        if self._in_docker:
            return f"""
## 🚨 PROJECT CREATION REMINDER

You are running in a **Docker container**. When creating new projects:

**MANDATORY**: Use the `setup_project` tool. DO NOT use mkdir or manual directory creation.

**Correct project location**: `{self._projects_path}/<project-name>/`

Example:
```yaml
setup_project:
  name: my-flask-api
  description: A Flask REST API
  framework: python
```

**FORBIDDEN**:
- `mkdir /tmp/my-project`
- `mkdir /projects/my-project`
- `os.makedirs("/tmp/...")`
- Creating directories outside `{self._projects_path}/`
"""
        else:
            return f"""
## 🚨 PROJECT CREATION REMINDER

When creating new projects:

**MANDATORY**: Use the `setup_project` tool. DO NOT use mkdir or manual directory creation.

**Correct project location**: `{self._projects_path}/<project-name>/`

Example:
```yaml
setup_project:
  name: my-flask-api
  description: A Flask REST API
  framework: python
```

**FORBIDDEN**:
- `mkdir /tmp/my-project`
- `mkdir /projects/my-project`
- `os.makedirs("/tmp/...")`
- Creating directories outside `usr/projects/`
"""
