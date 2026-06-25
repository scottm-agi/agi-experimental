"""
discover_skills — Tool for agents to discover and load available skills.

Skills are specialized instruction sets in .agents/skills/ directories.
Each skill has a SKILL.md file with YAML frontmatter (name, description)
and detailed markdown instructions.

Supports:
- list: List available skills in the project
- read: Read a specific skill's SKILL.md content
- fetch: Download a skill from a URL and save it to the project

This is a SEPARATE tool from read_instructions because skills represent
executable workflows, not static configuration.
"""
from __future__ import annotations

import os
import logging
from typing import Optional
from urllib.parse import urlparse
import re

from python.helpers.tool import Tool, Response
from python.helpers import files

logger = logging.getLogger("agix.discover_skills")


class DiscoverSkills(Tool):
    """
    Discover and load available skills from .agents/skills/ directories.
    
    Skills are folders containing SKILL.md files with instructions for
    specialized workflows (e.g., smoke testing, deployment, code review).
    
    Actions:
    - list: List all available skills with their names and descriptions
    - read: Read the full SKILL.md content for a specific skill
    - fetch: Download a skill from a URL and save to the project's .agents/skills/ directory
    """

    async def execute(self, action: str = "list", skill_name: str = None, **kwargs) -> Response:
        action = action or self.args.get("action", "list")
        skill_name = skill_name or self.args.get("skill_name", None)
        url = self.args.get("url", None)
        
        if action == "list":
            return await self._list_skills()
        elif action == "read":
            if not skill_name:
                return Response(
                    message="Error: 'skill_name' is required for 'read' action.",
                    break_loop=False
                )
            return await self._read_skill(skill_name)
        elif action == "fetch":
            if not url:
                return Response(
                    message="Error: 'url' is required for 'fetch' action. Provide the URL of a website or skill.md file.",
                    break_loop=False
                )
            return await self._fetch_skill(url)
        else:
            return Response(
                message=f"Error: Unknown action '{action}'. Use 'list', 'read', or 'fetch'.",
                break_loop=False
            )

    async def _list_skills(self) -> Response:
        """List all available skills with names and descriptions."""
        skills_dirs = self._find_skills_directories()
        
        if not skills_dirs:
            return Response(
                message="No skills directories found.",
                break_loop=False
            )
        
        skills = []
        for skills_dir in skills_dirs:
            for entry in sorted(os.listdir(skills_dir)):
                skill_path = os.path.join(skills_dir, entry)
                skill_md = os.path.join(skill_path, "SKILL.md")
                
                if os.path.isdir(skill_path) and os.path.isfile(skill_md):
                    name, description = self._parse_skill_frontmatter(skill_md)
                    skills.append({
                        "name": name or entry,
                        "directory": entry,
                        "description": description or "(no description)",
                        "path": skill_path,
                    })
        
        if not skills:
            return Response(
                message="No skills found in skills directories.",
                break_loop=False
            )
        
        # Format as readable list
        lines = ["## Available Skills\n"]
        for s in skills:
            lines.append(f"### {s['name']}")
            lines.append(f"- **Directory**: `{s['directory']}`")
            lines.append(f"- **Description**: {s['description']}")
            lines.append(f"- **Path**: `{s['path']}`")
            lines.append("")
        
        lines.append(f"\n*Use `discover_skills` with action='read' and skill_name='<directory>' to load full instructions.*")
        
        return Response(message="\n".join(lines), break_loop=False)

    async def _read_skill(self, skill_name: str) -> Response:
        """Read the full SKILL.md content for a specific skill."""
        skills_dirs = self._find_skills_directories()
        
        for skills_dir in skills_dirs:
            skill_path = os.path.join(skills_dir, skill_name)
            skill_md = os.path.join(skill_path, "SKILL.md")
            
            if os.path.isdir(skill_path) and os.path.isfile(skill_md):
                try:
                    content = files.read_file(skill_md)
                    if content:
                        return Response(
                            message=f"## Skill: {skill_name}\n\n{content}",
                            break_loop=False
                        )
                except Exception as e:
                    return Response(
                        message=f"Error reading skill '{skill_name}': {e}",
                        break_loop=False
                    )
        
        return Response(
            message=f"Skill '{skill_name}' not found. Use action='list' to see available skills.",
            break_loop=False
        )

    async def _fetch_skill(self, url: str) -> Response:
        """Fetch a skill from a URL and save it to the project's .agents/skills/ directory."""
        import urllib.request
        import ssl
        
        # Build list of URLs to try
        urls_to_try = []
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip('/')
        
        # If URL already ends with skill.md or SKILL.md, try it directly
        if path.lower().endswith('skill.md'):
            urls_to_try.append(url)
        else:
            # Try common skill.md locations
            urls_to_try.append(f"{base}{path}/skill.md")
            urls_to_try.append(f"{base}{path}/SKILL.md")
            urls_to_try.append(f"{base}/skill.md")
            urls_to_try.append(f"{base}/SKILL.md")
            # Also try the raw URL in case it's already the content
            urls_to_try.append(url)
        
        content = None
        fetched_url = None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        for try_url in urls_to_try:
            try:
                logger.info(f"Trying to fetch skill from: {try_url}")
                req = urllib.request.Request(try_url, headers={
                    'User-Agent': 'AGIX-SkillFetcher/1.0',
                    'Accept': 'text/markdown, text/plain, */*',
                })
                resp = urllib.request.urlopen(req, timeout=15, context=ctx)
                raw = resp.read().decode('utf-8', errors='replace')
                
                # Verify it looks like a skill file (has YAML frontmatter with name/description)
                if raw.strip().startswith('---') and ('name:' in raw[:500] or 'description:' in raw[:1000]):
                    content = raw
                    fetched_url = try_url
                    break
                # Also accept if it has markdown skill headers
                elif '# ' in raw[:200] and ('API' in raw or 'endpoint' in raw.lower() or 'register' in raw.lower()):
                    content = raw
                    fetched_url = try_url
                    break
                else:
                    logger.debug(f"URL {try_url} returned content but doesn't look like a skill")
            except Exception as e:
                logger.debug(f"Failed to fetch {try_url}: {e}")
                continue
        
        if not content:
            return Response(
                message=f"Could not find a SKILL.md at or near '{url}'. Tried: {', '.join(urls_to_try)}. "
                        f"Make sure the URL points to a website that serves a skill.md file.",
                break_loop=False
            )
        
        # Parse skill name from frontmatter
        skill_name = None
        if content.strip().startswith('---'):
            end_idx = content.find('---', 3)
            if end_idx != -1:
                frontmatter = content[3:end_idx]
                for line in frontmatter.split('\n'):
                    line = line.strip()
                    if line.startswith('name:'):
                        raw_name = line[5:].strip().strip('\'"')
                        # Sanitize for directory name
                        skill_name = re.sub(r'[^a-zA-Z0-9_-]', '-', raw_name).strip('-')
                        break
        
        # Fallback: derive name from the domain
        if not skill_name:
            skill_name = parsed.netloc.replace('.', '-').replace(':', '-')
        
        # Save to project's .agents/skills/<skill_name>/SKILL.md
        project_dir = self._get_project_dir()
        if not project_dir:
            project_dir = os.getcwd()
        
        skill_dir = os.path.join(project_dir, ".agents", "skills", skill_name)
        os.makedirs(skill_dir, exist_ok=True)
        
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"Skill '{skill_name}' saved to {skill_md_path}")
        
        # Parse description for the response
        _, description = self._parse_skill_frontmatter(skill_md_path)
        
        return Response(
            message=(
                f"✅ Skill fetched and installed to project!\n\n"
                f"- **Name**: {skill_name}\n"
                f"- **Source**: {fetched_url}\n"
                f"- **Saved to**: `{skill_md_path}`\n"
                f"- **Description**: {description or '(no description in frontmatter)'}\n\n"
                f"Use `discover_skills` with action='read' and skill_name='{skill_name}' to read the full instructions."
            ),
            break_loop=False
        )

    def _find_skills_directories(self) -> list:
        """Find all .agents/skills/ directories from project root upward."""
        dirs = []
        
        # Check project directory
        project_dir = self._get_project_dir()
        if project_dir:
            agents_skills = os.path.join(project_dir, ".agents", "skills")
            if os.path.isdir(agents_skills):
                dirs.append(agents_skills)
        
        # Also check cwd-based paths
        cwd = os.getcwd()
        cwd_skills = os.path.join(cwd, ".agents", "skills")
        if os.path.isdir(cwd_skills) and cwd_skills not in dirs:
            dirs.append(cwd_skills)
        
        # Check parent directories (up to 3 levels)
        current = cwd
        for _ in range(3):
            parent = os.path.dirname(current)
            if parent == current:
                break
            parent_skills = os.path.join(parent, ".agents", "skills")
            if os.path.isdir(parent_skills) and parent_skills not in dirs:
                dirs.append(parent_skills)
            current = parent
        
        return dirs

    def _parse_skill_frontmatter(self, skill_md_path: str) -> tuple:
        """Parse YAML frontmatter from SKILL.md to extract name and description."""
        try:
            content = files.read_file(skill_md_path)
            if not content or not content.startswith("---"):
                return (None, None)
            
            # Find end of frontmatter
            end_idx = content.find("---", 3)
            if end_idx == -1:
                return (None, None)
            
            frontmatter = content[3:end_idx].strip()
            name = None
            description = None
            
            for line in frontmatter.split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    name = line[5:].strip().strip("'\"")
                elif line.startswith("description:"):
                    description = line[12:].strip().strip("'\"")
            
            return (name, description)
        except Exception:
            return (None, None)

    def _get_project_dir(self) -> Optional[str]:
        """Get the current project working directory from agent context."""
        try:
            if self.agent:
                active_project = getattr(self.agent, 'data', {}).get('active_project')
                if isinstance(active_project, dict) and active_project.get('path'):
                    return active_project['path']
                
                from python.helpers import projects as projects_helper
                context = getattr(self.agent, 'context', None)
                if context:
                    project_name = projects_helper.get_context_project_name(context)
                    if project_name:
                        project_dir = projects_helper.get_project_dir(project_name)
                        if project_dir:
                            return project_dir
        except Exception:
            pass
        return os.getcwd()
