import os
import yaml
import re
from typing import List, Dict, Optional, Any


class SkillMetadata:
    def __init__(self, name: str, description: str, path: str, source: str,
                 mode: Optional[str] = None,
                 requires_tools: Optional[List[str]] = None,
                 requires_context_type: Optional[List[str]] = None,
                 triggers: Optional[List[str]] = None,
                 anti_triggers: Optional[List[str]] = None,
                 trigger_patterns: Optional[List[str]] = None,
                 skill_type: Optional[str] = None,
                 references: Optional[List[str]] = None):
        self.name = name
        self.description = description
        self.path = path  # Absolute path to SKILL.md
        self.source = source  # 'global' or 'project'
        self.mode = mode  # Optional: specific mode slug
        # Phase 2B: Conditional activation frontmatter
        self.requires_tools = requires_tools or []
        self.requires_context_type = requires_context_type or []
        # Skill activation frontmatter (3-layer matching)
        self.triggers = triggers or []
        self.anti_triggers = anti_triggers or []
        self.trigger_patterns = trigger_patterns or []
        self.skill_type = skill_type
        # Route-first architecture: reference document filenames
        self.references = references or []


class SkillContent(SkillMetadata):
    def __init__(self, name: str, description: str, path: str, source: str,
                 instructions: str, mode: Optional[str] = None,
                 requires_tools: Optional[List[str]] = None,
                 requires_context_type: Optional[List[str]] = None,
                 triggers: Optional[List[str]] = None,
                 anti_triggers: Optional[List[str]] = None,
                 trigger_patterns: Optional[List[str]] = None,
                 skill_type: Optional[str] = None,
                 references: Optional[List[str]] = None):
        super().__init__(name, description, path, source, mode,
                         requires_tools, requires_context_type,
                         triggers, anti_triggers, trigger_patterns, skill_type,
                         references)
        self.instructions = instructions


class SkillsManager:
    def __init__(self, project_root: str = None, global_dir: str = None):
        self.project_root = project_root or os.getcwd()
        # Default global dir to /agix/data/skills if in container, else /agix/data/skills, else ~/.config/agix/skills
        if not global_dir:
            if os.path.exists("/agix/data"):
                global_dir = "/agix/data/skills"
            elif os.path.exists("/agix/data"):
                global_dir = "/agix/data/skills"
            else:
                global_dir = os.path.expanduser("~/.config/agix/skills")
        self.global_dir = global_dir
        self.skills: Dict[str, SkillMetadata] = {}

    def discover_skills(self, force: bool = False):
        """Discover skills from bundled, global, and project directories."""
        if hasattr(self, '_discovered') and self._discovered and not force:
            return
        
        self.skills = {}
        
        # 0. Discover Bundled Skills (repo root /skills/ — version-controlled base skills)
        # These are base skills that ship with the repo. Priority: project > bundled > global
        bundled_dir = self._find_bundled_skills_dir()
        if bundled_dir and os.path.exists(bundled_dir):
            self._scan_directory(bundled_dir, "bundled")
        
        # 1. Discover Global Skills (runtime data/skills/ — user-created skills)
        if os.path.exists(self.global_dir):
            self._scan_directory(self.global_dir, "global")
            
        # 2. Discover Project Skills (.roo/skills)
        project_skills_dir = os.path.join(self.project_root, ".roo", "skills")
        if os.path.exists(project_skills_dir):
            self._scan_directory(project_skills_dir, "project")
            
        # 3. Discover Mode-Specific Project Skills (.roo/skills-{mode})
        # This will be handled during scanning if we find directories starting with skills-
        project_roo_dir = os.path.join(self.project_root, ".roo")
        if os.path.exists(project_roo_dir):
            for entry in os.listdir(project_roo_dir):
                if entry.startswith("skills-") and os.path.isdir(os.path.join(project_roo_dir, entry)):
                    mode = entry.replace("skills-", "")
                    self._scan_directory(os.path.join(project_roo_dir, entry), "project", mode)

        # 4. Discover Profile-Specific Skills (agents/{profile}/skills/)
        agents_dir = self._find_agents_dir()
        if agents_dir and os.path.isdir(agents_dir):
            for profile_name in os.listdir(agents_dir):
                profile_skills = os.path.join(agents_dir, profile_name, "skills")
                if os.path.isdir(profile_skills):
                    self._scan_directory(profile_skills, "profile")

        self._discovered = True

    def _find_bundled_skills_dir(self) -> Optional[str]:
        """Find the bundled skills directory at the repo root."""
        # In container: /agix/skills/ or /agix/skills/
        for base in ["/agix", "/agix"]:
            candidate = os.path.join(base, "skills")
            if os.path.isdir(candidate):
                return candidate
        # Local dev: walk up from project_root looking for skills/ dir
        search_dir = self.project_root
        for _ in range(5):  # Max 5 levels up
            candidate = os.path.join(search_dir, "skills")
            if os.path.isdir(candidate):
                return candidate
            parent = os.path.dirname(search_dir)
            if parent == search_dir:
                break
            search_dir = parent
        return None

    def _find_agents_dir(self) -> Optional[str]:
        """Find the agents/ directory at the repo root."""
        # In container: /agix/agents/ or /agix/agents/
        for base in ["/agix", "/agix"]:
            candidate = os.path.join(base, "agents")
            if os.path.isdir(candidate):
                return candidate
        # Local dev: walk up from project_root looking for agents/ dir
        search_dir = self.project_root
        for _ in range(5):  # Max 5 levels up
            candidate = os.path.join(search_dir, "agents")
            if os.path.isdir(candidate):
                return candidate
            parent = os.path.dirname(search_dir)
            if parent == search_dir:
                break
            search_dir = parent
        return None

    def _scan_directory(self, dir_path: str, source: str, mode: Optional[str] = None):
        """Scan a directory for skill folders."""
        if not os.path.isdir(dir_path):
            return

        for entry in os.listdir(dir_path):
            entry_path = os.path.join(dir_path, entry)
            if os.path.isdir(entry_path):
                skill = self._parse_skill(entry_path, source, mode)
                if skill:
                    # Priority: project > global, mode-specific > generic
                    existing = self.skills.get(skill.name)
                    if not existing or self._should_override(existing, skill):
                        self.skills[skill.name] = skill

    def _should_override(self, existing: SkillMetadata, new_skill: SkillMetadata) -> bool:
        """Priority rules: project > global > bundled, mode-specific > generic."""
        priority = {"bundled": 0, "global": 1, "profile": 1.5, "project": 2}
        existing_priority = priority.get(existing.source, 0)
        new_priority = priority.get(new_skill.source, 0)
        if new_priority > existing_priority:
            return True
        if new_priority == existing_priority:
            if new_skill.mode and not existing.mode:
                return True
        return False

    def _parse_skill(self, skill_dir: str, source: str, mode: Optional[str] = None) -> Optional[SkillMetadata]:
        """Parse SKILL.md in a skill directory."""
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        if not os.path.exists(skill_md_path):
            return None

        try:
            with open(skill_md_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Regex to match YAML frontmatter
            match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)
            if not match:
                return None

            frontmatter_raw = match.group(1)
            instructions = match.group(2).strip()

            metadata = yaml.safe_load(frontmatter_raw)
            name = metadata.get('name')
            description = metadata.get('description')

            if not name or not description:
                return None

            # Spec validation: name must match directory name
            if name != os.path.basename(skill_dir):
                return None

            # Spec validation: name format
            if not re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', name) or len(name) > 64:
                return None

            # Phase 2B: Parse conditional activation fields
            requires_tools = metadata.get('requires_tools', [])
            requires_context_type = metadata.get('requires_context_type', [])
            
            # Normalize to lists
            if isinstance(requires_tools, str):
                requires_tools = [requires_tools]
            if isinstance(requires_context_type, str):
                requires_context_type = [requires_context_type]

            # Skill activation frontmatter (3-layer matching)
            triggers = metadata.get('triggers', [])
            anti_triggers = metadata.get('anti_triggers', [])
            trigger_patterns = metadata.get('trigger_patterns', [])
            skill_type = metadata.get('skill_type', None)

            # Normalize to lists
            if isinstance(triggers, str):
                triggers = [triggers]
            if isinstance(anti_triggers, str):
                anti_triggers = [anti_triggers]
            if isinstance(trigger_patterns, str):
                trigger_patterns = [trigger_patterns]

            # Discover references/ directory (route-first architecture)
            refs_dir = os.path.join(skill_dir, "references")
            references = []
            if os.path.isdir(refs_dir):
                references = sorted([
                    f for f in os.listdir(refs_dir)
                    if os.path.isfile(os.path.join(refs_dir, f)) and f.endswith('.md')
                ])

            return SkillContent(
                name, description, skill_md_path, source, instructions, mode,
                requires_tools=requires_tools or [],
                requires_context_type=requires_context_type or [],
                triggers=triggers or [],
                anti_triggers=anti_triggers or [],
                trigger_patterns=trigger_patterns or [],
                skill_type=skill_type,
                references=references,
            )
        except Exception as e:
            # In production, we'd log this properly
            return None

    def get_skills_for_mode(self, mode: str) -> List[SkillMetadata]:
        """Get all skills applicable to a given mode."""
        applicable_skills = []
        for skill in self.skills.values():
            if not skill.mode or skill.mode == mode:
                applicable_skills.append(skill)
        return applicable_skills

    def get_skill_content(self, name: str) -> Optional[SkillContent]:
        """Retrieve full skill content by name."""
        return self.skills.get(name) if isinstance(self.skills.get(name), SkillContent) else None

    # =========================================================================
    # Phase 2A: Progressive Discovery — Compact Index
    # =========================================================================

    def get_compact_index(self, mode: str = "default",
                          available_tools: Optional[List[str]] = None,
                          context_type: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Return a compact index of skills: [{name, description}] only.
        
        When available_tools or context_type are provided, applies Phase 2B
        conditional filtering before building the index.
        
        No instruction bodies are included — agents use view_skill tool
        to load full instructions on demand.
        """
        if available_tools is not None or context_type is not None:
            skills = self.get_skills_filtered(mode, available_tools or [], context_type or "user")
        else:
            skills = self.get_skills_for_mode(mode)
        
        return [
            {"name": s.name, "description": s.description}
            for s in skills
        ]

    def get_compact_index_prompt(self, mode: str = "default",
                                  available_tools: Optional[List[str]] = None,
                                  context_type: Optional[str] = None) -> str:
        """
        Return a formatted prompt string for system prompt injection.
        
        Contains a compact skills index and directive to use view_skill tool.
        This replaces the old pattern of baking full instruction bodies
        into the system prompt (~24K token savings).
        """
        index = self.get_compact_index(mode, available_tools, context_type)
        
        if not index:
            return ""
        
        lines = ["## Available Skills", ""]
        lines.append("The following skills are available. Use the `view_skill` tool with the skill name to load full instructions when needed.")
        lines.append("")
        
        for item in index:
            lines.append(f"- **{item['name']}**: {item['description']}")
        
        lines.append("")
        lines.append("*Load a skill's full instructions only when you need them for your current task.*")
        
        return "\n".join(lines)

    # =========================================================================
    # Phase 2B: Conditional Activation — Filtered Skills
    # =========================================================================

    def get_skills_filtered(self, mode: str = "default",
                             available_tools: Optional[List[str]] = None,
                             context_type: Optional[str] = None) -> List[SkillMetadata]:
        """
        Get skills filtered by mode, available tools, and context type.
        
        Filtering rules:
        - Skills without requires_tools/requires_context_type are always included
        - requires_tools: ALL listed tools must be present in available_tools
        - requires_context_type: context_type must be in the list
        - Both conditions must be satisfied (AND logic)
        """
        mode_skills = self.get_skills_for_mode(mode)
        available_tools = available_tools or []
        context_type = context_type or ""
        
        filtered = []
        for skill in mode_skills:
            # Check requires_tools — all must be available
            if skill.requires_tools:
                if not all(tool in available_tools for tool in skill.requires_tools):
                    continue
            
            # Check requires_context_type — context must match one
            if skill.requires_context_type:
                if context_type.lower() not in [ct.lower() for ct in skill.requires_context_type]:
                    continue
            
            filtered.append(skill)
        
        return filtered

    # =========================================================================
    # 3-Layer Skill Activation Engine
    # =========================================================================

    def match_skills(self, prompt: str, min_score: float = 0.5) -> List[Dict[str, Any]]:
        """
        Match a user prompt against all loaded skills using 3-layer activation.

        Layer 1 — Keyword triggers (weight=3): Case-insensitive substring match.
        Layer 2 — Regex patterns (weight=5): trigger_patterns matched against prompt.
        Layer 3 — Semantic description (weight=1): Word overlap between prompt and
                  skill description, normalized by description length.

        Anti-triggers apply a penalty (weight=-10) to suppress false positives.

        Returns:
            Sorted list of dicts [{"name", "skill", "score", "layers_hit"}]
            Only skills with score >= min_score are returned.
        """
        prompt_lower = prompt.lower()
        results: List[Dict[str, Any]] = []

        for skill in self.skills.values():
            score = 0.0
            layers_hit: List[str] = []

            # --- Anti-trigger penalty ---
            anti_penalty = 0.0
            for anti in skill.anti_triggers:
                if anti.lower() in prompt_lower:
                    anti_penalty += 10.0
            if anti_penalty > 0:
                layers_hit.append("anti_trigger")

            # --- Layer 1: Keyword triggers ---
            keyword_hits = 0
            for trigger in skill.triggers:
                if trigger.lower() in prompt_lower:
                    keyword_hits += 1
            if keyword_hits > 0:
                score += keyword_hits * 3.0
                layers_hit.append("keyword")

            # --- Layer 2: Regex patterns ---
            pattern_hits = 0
            for pattern in skill.trigger_patterns:
                try:
                    if re.search(pattern, prompt, re.IGNORECASE):
                        pattern_hits += 1
                except re.error:
                    pass  # Skip malformed patterns
            if pattern_hits > 0:
                score += pattern_hits * 5.0
                layers_hit.append("pattern")

            # --- Layer 3: Semantic description word overlap ---
            if skill.description:
                desc_words = set(re.findall(r'\b[a-z]{3,}\b', skill.description.lower()))
                prompt_words = set(re.findall(r'\b[a-z]{3,}\b', prompt_lower))
                if desc_words:
                    overlap = len(desc_words & prompt_words)
                    semantic_score = (overlap / len(desc_words)) * 5.0
                    if semantic_score > 0:
                        score += semantic_score
                        layers_hit.append("semantic")

            # Apply anti-trigger penalty
            score -= anti_penalty

            if score >= min_score:
                results.append({
                    "name": skill.name,
                    "skill": skill,
                    "score": round(score, 2),
                    "layers_hit": layers_hit,
                })

        # Sort by score descending
        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    def best_match_skill(self, prompt: str, min_score: float = 0.5) -> Optional[Dict[str, Any]]:
        """
        Return the single best-matching skill for a prompt, or None.
        
        Convenience wrapper around match_skills().
        """
        matches = self.match_skills(prompt, min_score=min_score)
        return matches[0] if matches else None

    # =========================================================================
    # Route-First Architecture: Reference Document Support
    # =========================================================================

    def get_skill_references(self, name: str) -> List[str]:
        """
        Get list of reference document filenames for a skill.
        
        Returns empty list if skill doesn't exist or has no references/ dir.
        """
        skill = self.skills.get(name)
        if not skill:
            return []
        return skill.references if hasattr(skill, 'references') else []

    def get_reference_content(self, skill_name: str, ref_name: str) -> Optional[str]:
        """
        Load the content of a specific reference document.
        
        Args:
            skill_name: Name of the skill (e.g., 'devops')
            ref_name: Reference filename (e.g., 'docker.md')
        
        Returns:
            Markdown content of the reference, or None if not found.
        """
        skill = self.skills.get(skill_name)
        if not skill:
            return None
        
        # Derive skill directory from SKILL.md path
        skill_dir = os.path.dirname(skill.path)
        ref_path = os.path.join(skill_dir, "references", ref_name)
        
        if not os.path.isfile(ref_path):
            return None
        
        try:
            with open(ref_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return None

    # =========================================================================
    # Skill Auto-Activation: Prompt-Based Automatic Skill Injection
    # =========================================================================

    def auto_activate_for_prompt(
        self, prompt: str, min_score: float = 3.0
    ) -> Optional[Dict[str, Any]]:
        """Auto-detect the best matching skill for a user prompt.

        Uses the 3-layer matching engine (match_skills) to find the best skill
        for the given prompt. Returns the skill metadata + full instructions
        if the score exceeds the threshold.

        This is called during system prompt building to automatically inject
        skill instructions without requiring the LLM to call discover_skills.

        Args:
            prompt: The user's input message.
            min_score: Minimum activation score (default 3.0).

        Returns:
            Dict with 'name', 'score', 'layers_hit', 'instructions', or None.
        """
        if not prompt or len(prompt) < 20:
            return None

        matches = self.match_skills(prompt, min_score=min_score)
        if not matches:
            return None

        best = matches[0]
        skill = best.get("skill")
        if not skill:
            return None

        # Get full instruction body
        instructions = ""
        if isinstance(skill, SkillContent):
            instructions = skill.instructions
        else:
            # Try loading from disk
            content = self.get_skill_content(best["name"])
            if content:
                instructions = content.instructions

        if not instructions:
            return None

        return {
            "name": best["name"],
            "score": best["score"],
            "layers_hit": best["layers_hit"],
            "instructions": instructions,
        }

    def build_auto_activation_prompt(
        self, activation: Dict[str, Any], role: str = "orchestrator"
    ) -> str:
        """Build a prompt section for an auto-activated skill.

        Args:
            activation: Result from auto_activate_for_prompt().
            role: 'orchestrator' for full body, 'subordinate' for reference.

        Returns:
            Formatted markdown prompt section.
        """
        name = activation.get("name", "unknown")
        score = activation.get("score", 0)
        layers = activation.get("layers_hit", [])
        instructions = activation.get("instructions", "")

        if role == "orchestrator":
            # Full skill body for orchestrators — they need the phase pipeline
            return (
                f"\n## 🔴 AUTO-ACTIVATED SKILL: {name}\n"
                f"*Matched with score={score}, layers={layers}*\n\n"
                f"**You MUST follow this skill's phases and rules for this task.**\n"
                f"Do NOT use the generic decomposition — use the phases below.\n\n"
                f"{instructions}\n"
            )
        else:
            # Concise reference for subordinates — key rules only
            # Extract first 2000 chars or first 3 sections
            lines = instructions.split('\n')
            preview_lines = []
            section_count = 0
            for line in lines:
                if line.startswith('## '):
                    section_count += 1
                    if section_count > 3:
                        break
                preview_lines.append(line)

            preview = '\n'.join(preview_lines[:50])  # Cap at 50 lines
            return (
                f"\n## 📋 SKILL REFERENCE: {name}\n"
                f"*Auto-matched for this task (score={score})*\n\n"
                f"**Key guidelines from the matched skill:**\n\n"
                f"{preview}\n\n"
                f"*Use `view_skill` with name='{name}' for full instructions.*\n"
            )

