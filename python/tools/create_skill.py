import os
import re
import yaml
from python.helpers.tool import Tool, Response
from python.helpers import files

class CreateSkill(Tool):
    async def execute(self, **kwargs) -> Response:
        skill_name = self.args.get("skill_name")
        description = self.args.get("description")
        instructions = self.args.get("instructions")
        modes = self.args.get("modes", [])
        is_global = self.args.get("is_global", False)

        if not skill_name:
            return Response(message="Error: Missing 'skill_name' argument.", break_loop=False)
        if not description:
            return Response(message="Error: Missing 'description' argument.", break_loop=False)
        if not instructions:
            return Response(message="Error: Missing 'instructions' argument.", break_loop=False)

        # Validate skill name format (kebab-case)
        if not re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', skill_name):
            return Response(message="Error: 'skill_name' must be kebab-case (e.g., 'my-skill-name').", break_loop=False)

        try:
            # Determine target directory
            if is_global:
                skills_dir = self.agent.skills_manager.global_dir
            else:
                skills_dir = os.path.join(self.agent.skills_manager.project_root, ".roo", "skills")

            skill_path = os.path.join(skills_dir, skill_name)
            os.makedirs(skill_path, exist_ok=True)
            os.makedirs(os.path.join(skill_path, "resources"), exist_ok=True)
            os.makedirs(os.path.join(skill_path, "instructions"), exist_ok=True)

            # Generate SKILL.md content
            metadata = {
                "name": skill_name,
                "description": description
            }
            if modes:
                metadata["modes"] = modes

            frontmatter = yaml.dump(metadata, sort_keys=False).strip()
            
            skill_md_content = f"""---
{frontmatter}
---

# {skill_name.replace('-', ' ').title()}

## Executive Summary
{description}

## Instructions
{instructions}

## Lifecycle Hooks
- `init`: [TBD]
- `execute`: [TBD]
- `validate`: [TBD]

## Resources
- [resources/](file://{os.path.join(skill_path, "resources")})
"""
            skill_md_file = os.path.join(skill_path, "SKILL.md")
            files.write_file_atomic(skill_md_file, skill_md_content.strip())

            # Reload skills for the agent
            self.agent.skills_manager.discover_skills()

            source = "global" if is_global else "project"
            return Response(
                message=f"Successfully created {source} skill '{skill_name}' at {skill_path}.\nThe skill has been automatically reloaded and is now available to use.",
                break_loop=False
            )

        except Exception as e:
            return Response(message=f"Error creating skill '{skill_name}': {str(e)}", break_loop=False)
