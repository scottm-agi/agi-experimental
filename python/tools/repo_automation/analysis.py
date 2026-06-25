"""
Analysis module for repository automation.
Contains functions for issue analysis, context extraction, and expert analysis generation.
"""
from __future__ import annotations
import os
import re
import logging
import requests
import subprocess
import shutil
from typing import Dict, Any, List, TYPE_CHECKING

from .base import DEFAULT_EXCLUDES, ROOT_INCLUDES, MAX_CODEBASE_WORDS, logger
from python.helpers.output_truncation import truncate_output_middle_out

if TYPE_CHECKING:
    from python.agent import Agent


def parse_issue_text(text: str) -> Dict[str, Any]:
    """Simple parser for issue output."""
    data = {
        "number": "N/A",
        "title": "Unknown Issue",
        "body": text,
        "reporter": "Unknown",
        "created_at": "N/A"
    }
    lines = text.splitlines()
    if lines:
        line = lines[0]
        num_match = re.search(r'#(\d+)', line)
        if num_match:
            data["number"] = num_match.group(1)
            data["title"] = line.split(":", 1)[1].strip() if ":" in line else line
    return data


def validate_analysis(content: str) -> bool:
    """Check that analysis has real content, not placeholder text."""
    placeholders = [
        "[Assessed from context]", "[Identified risks]", "[Why this matters]",
        "[Your diagram here]", "[Feature behaves as expected]", "[Research/Setup]"
    ]
    
    for p in placeholders:
        if p in content:
            logger.warning(f"Analysis contains placeholder: {p}")
            return False
    
    if len(content) < 500:
        logger.warning(f"Analysis too short: {len(content)} chars")
        return False
        
    return True


def format_sources_for_prompt(sources: List[Dict[str, Any]]) -> str:
    """Format the available sources into a clear list for LLM grounding."""
    if not sources:
        return ""
    
    output = "\n## AVAILABLE SOURCES FOR GROUNDING\n"
    output += "You MUST use [N] inline citations referencing these IDs and include a ## Sources footer.\n"
    for s in sources:
        if s["type"] == "url":
            output += f"[{s['id']}] {s['url']} ({s['description']})\n"
        else:
            output += f"[{s['id']}] {s['path']} ({s['description']})\n"
    return output


async def extract_relevant_context(
    issue_text: str, 
    codebase: str, 
    project_path: str,
    ripgrep_search_fn=None
) -> str:
    """
    Heuristic extraction of relevant files and symbols.
    Includes URL crawling for external documentation links.
    
    Args:
        issue_text: Combined issue body and comments
        codebase: Full codebase context string
        project_path: Path to project directory
        ripgrep_search_fn: Optional ripgrep search function for code search
    """
    context = {
        "tech_stack": [],
        "matching_files": [],
        "crawled_content": [],
        "code_snippets": []
    }
    messages = []
    
    # 1. URL Crawling: Scan for all documentation links
    urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', issue_text)
    urls = list(dict.fromkeys(urls))
    for url in urls[:10]:
        try:
            logger.debug(f"Crawling URL for context: {url}")
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                text = re.sub('<[^<]+?>', '', resp.text)
                context["crawled_content"].append(f"Content from {url}:\n{text[:2000]}")
        except Exception as e:
            logger.warning(f"Failed to crawl {url}: {e}")

    # 2. Detect tech stack
    stack_indicators = {
        "Python": [".py", "pytest", "flask", "django", "fastapi"],
        "JavaScript": [".js", "npm", "react", "node", "vue"],
        "TypeScript": [".ts", "tsx"],
        "Docker": ["Dockerfile", "docker-compose"],
        "Forgejo/Gitea": ["forgejo", "gitea", "mcp"],
        "MISE": [".mise.toml", "mise exec"]
    }
    for tech, indicators in stack_indicators.items():
        if any(ind in issue_text.lower() or ind in codebase.lower() for ind in indicators):
            context["tech_stack"].append(tech)
    
    # 3. Keyword-based file matching from analyzed context
    file_pattern = r'### `([^`]+)`'
    all_files = re.findall(file_pattern, codebase)
    
    # a. Prioritize core files
    core_keywords = ["readme", "main", "index", "app", "config", "package.json", "requirements.txt"]
    for f in all_files:
        if any(key in f.lower() for key in core_keywords):
            context["matching_files"].append(f)

    # b. Match based on issue keywords
    issue_words = set(re.findall(r'\w+', issue_text.lower()))
    common_noisy_terms = {"body", "text", "name", "type", "data", "file", "user", "base", "path"}
    issue_words = {w for w in issue_words if len(w) > 3 and not w.isdigit() and w not in common_noisy_terms}
    
    for f in all_files:
        f_lower = f.lower()
        if any(word in f_lower for word in issue_words):
            if any(x in f_lower for x in ["vendor", "node_modules", "tmp", "venv", ".min."]):
                continue
            context["matching_files"].append(f)

    # 4. Use ripgrep for deeper symbol search (if function provided)
    if ripgrep_search_fn and os.path.exists(project_path):
        interesting_symbols = [w for w in issue_words if "_" in w or any(c.isupper() for c in w)]
        if not interesting_symbols:
            interesting_symbols = list(issue_words)[:3]
        
        file_types = []
        if "Python" in context.get("tech_stack", []):
            file_types.append("py")
        if any(t in context.get("tech_stack", []) for t in ["JavaScript", "TypeScript"]):
            file_types.extend(["js", "ts"])
        
        for sym in interesting_symbols[:5]:
            try:
                pattern = f"(def|class|function|const|let|var)\\s+{sym}"
                matches = ripgrep_search_fn(pattern, project_path, 
                                           file_types=file_types if file_types else None,
                                           max_results=5)
                for match in matches:
                    if match.get("file"):
                        context["matching_files"].append(match["file"])
                        if match.get("content"):
                            context["code_snippets"].append(
                                f"Match in `{match['file']}` (line {match['line']}):\n```\n{match['content']}\n```"
                            )
            except Exception:
                continue
    
    # 5. Read content of top matching files
    for f in context["matching_files"][:5]:
        try:
            abs_f = os.path.join(project_path, f)
            if os.path.exists(abs_f) and os.path.isfile(abs_f):
                with open(abs_f, "r") as file_handle:
                    content = file_handle.read(5000)
                    context["code_snippets"].append(f"Content of `{f}`:\n```\n{content}\n```")
        except Exception as e:
            logger.warning(f"Failed to read file {f} for context: {e}")
    
    # Unique and cleanup matching files
    context["matching_files"] = list(dict.fromkeys(context["matching_files"]))
    context["matching_files"] = [
        f for f in context["matching_files"] 
        if not any(x in f.lower() for x in ["vendor", "node_modules", "tmp", "venv", ".min."])
    ]

    # Structure sources for LLM grounding
    sources = []
    for i, url in enumerate(urls[:10]):
        sources.append({"id": i + 1, "type": "url", "url": url, "description": f"Documentation at {url}"})
    
    for i, f in enumerate(context["matching_files"][:15]):
        sources.append({"id": len(urls[:10]) + i + 1, "type": "file", "path": f, "description": f"Source file: {f}"})

    if context["crawled_content"]:
        messages.append("**External Context Fetched**:\n" + "\n\n".join(context["crawled_content"]))

    if context["matching_files"]:
        messages.append("**Relevant Files Found**:\n" + "\n".join([f"- {f}" for f in context["matching_files"][:15]]))
        
    if context["code_snippets"]:
        messages.append("**Extracted Code Context**:\n" + "\n\n".join(context["code_snippets"]))
        
    messages.append(format_sources_for_prompt(sources))
        
    return "\n\n".join(messages)


async def generate_expert_analysis(
    issue_data: Dict[str, Any], 
    context: str,
    agent: "Agent" = None,
    research_fn=None
) -> str:
    """
    Generate expert analysis using LLM.
    
    Args:
        issue_data: Parsed issue data dict
        context: Relevant context string
        agent: Agent instance for accessing LLM config
        research_fn: Optional Perplexity research function
    """
    issue_num = issue_data.get('number', 'N/A')
    issue_title = issue_data.get('title', 'Unknown')
    issue_body = truncate_output_middle_out(issue_data.get('body', 'No description'), max_chars=2000, head_ratio=0.3)
    
    # Detect if the issue body contains questions that need answering
    question_indicators = ['?', 'explain', 'why', 'what is', 'how do', 'can we', 'should we', 'business value', 'priority']
    has_question = any(ind.lower() in issue_body.lower() for ind in question_indicators)
    
    # Detect research intents that should trigger Perplexity search
    research_indicators = [
        'research', 'market', 'addressable', 'tam', 'sam', 'som', 'competitor', 
        'industry', 'trend', 'statistics', 'data', 'benchmark', 'comparison',
        'growth', 'revenue', 'pricing', 'market size', 'market share', 'forecast'
    ]
    has_research_intent = any(ind.lower() in issue_body.lower() for ind in research_indicators)
    
    # If research intent detected, call Perplexity for real market data
    research_data = ""
    if has_research_intent and research_fn:
        try:
            research_query = f"For a software feature about '{issue_title}': {issue_body[:500]}. Provide market research including: addressable market size (TAM/SAM/SOM), competitor landscape, industry trends, growth projections, and relevant statistics."
            logger.info(f"[EXPERT] Research intent detected for #{issue_num}, calling Perplexity")
            research_data = research_fn(research_query)
            if research_data:
                logger.info(f"[EXPERT] Perplexity returned {len(research_data)} chars of research data")
        except Exception as e:
            logger.warning(f"[EXPERT] Perplexity research failed: {e}")
            research_data = ""
    
    prompt = f"""You are an expert software architect and product strategist. Analyze this issue and return ONLY a JSON object with the following structure. DO NOT return markdown, ONLY valid JSON.

## Issue to Analyze
**Issue #{issue_num}**: {issue_title}

**Description**:
{issue_body}

## Codebase Context
{truncate_output_middle_out(context, max_chars=48000, head_ratio=0.2)}

---

IMPORTANT: If the description contains QUESTIONS, you MUST answer them directly in the "question_answer" field.

Return ONLY this JSON structure (no markdown, no explanation):
{{
    "summary": "<1-2 sentence technical summary of the issue>",
    "question_answer": "<If the description contains questions (marked with ? or asking 'why', 'what', 'how', 'explain', etc), provide a DIRECT ANSWER here. Otherwise leave empty.>",
    "business_value": "<Explain the business impact: user benefits, revenue potential, risk reduction, competitive advantage, efficiency gains>",
    "priority_rationale": "<Why this priority level? What happens if delayed? Dependencies?>",
    "user_impact": "<How does this affect end users? UX improvement, new capability, pain point solved?>",
    "components_affected": ["<component1>", "<component2>"],
    "technical_strategy": "<How to approach this with TDD focus>",
    "issue_type": "<bug|feature|performance|refactor>",
    "specialized_analysis": "<If bug: root cause. If feature: architecture. If performance: bottleneck.>",
    "mermaid_diagram": "<valid mermaid graph TD diagram showing the flow>",
    "implementation_phases": [
        {{"phase": "Setup", "action": "<action>", "files": ["<file>"], "rationale": "<why>"}},
        {{"phase": "<phase>", "action": "<action>", "files": ["<file>"], "rationale": "<why>"}}
    ],
    "code_pattern": "<code snippet showing the pattern to use>",
    "code_language": "<python|javascript|typescript|etc>",
    "risks": [
        {{"risk": "<risk>", "impact": "High|Med|Low", "mitigation": "<strategy>"}}
    ],
    "edge_cases": [
        "<edge case 1 description>",
        "<edge case 2 description>",
        "<edge case 3 description>"
    ],
    "regression_prevention": "<how to prevent old bugs from returning>",
    "manual_test_plan": "<Step-by-step point & click plan for human verification>",
    "sources": ["<source1>", "<source2>"]
}}
"""
    
    # Use the agent's LLM to generate the analysis
    try:
        from python.helpers.call_llm import call_llm
        from python.models import get_chat_model
        from python.helpers import files
        
        # Load multiagentdev profile components
        try:
            profile_context = files.read_prompt_file("_context.md", [files.get_abs_path("agents", "multiagentdev")])
            orig_profile = agent.config.profile
            agent.config.profile = "multiagentdev"
            try:
                role_prompt = agent.read_prompt("agent.system.main.role.md")
            finally:
                agent.config.profile = orig_profile
            
            system_prompt = f"{profile_context}\n\n{role_prompt}\n\nYou are an expert software analyst. Generate complete, detailed analysis with NO placeholder text. Fill in ALL sections with real, specific content based on the codebase context provided."
            chat_model = get_chat_model("role", "multiagentdev")
            logger.info("[PHASE 3] Using multiagentdev profile for expert analysis")
        except Exception as e:
            logger.warning(f"multiagentdev profile load failed: {e}. Falling back to default model.")
            chat_config = agent.config.chat_model
            chat_model = get_chat_model(chat_config.provider, chat_config.name)
            system_prompt = "You are an expert software analyst. Generate complete, detailed analysis with NO placeholder text. Fill in ALL sections with real, specific content based on the codebase context provided."

        response = await call_llm(
            system=system_prompt,
            model=chat_model,
            message=prompt
        )
        
        raw_response = str(response) if response else ""
        
        # Parse JSON from LLM response
        import json
        
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```|(\{[\s\S]*\})', raw_response)
        if json_match:
            json_str = json_match.group(1) or json_match.group(2)
        else:
            json_str = raw_response.strip()
        
        try:
            data = json.loads(json_str)
            logger.info(f"[EXPERT] Successfully parsed JSON data for issue #{issue_num}")
        except json.JSONDecodeError as e:
            logger.warning(f"[EXPERT] JSON parse failed: {e}, using fallback")
            raise ValueError("JSON parse failed")
        
        # Render template with LLM data
        analysis = _render_expert_template(data, issue_num, issue_title, issue_body, context, research_data)
        
        logger.info(f"[EXPERT] Template rendered successfully ({len(analysis)} chars)")
        
        # Manual vaulting for large responses
        if len(analysis) > 10000:
            from python.helpers.hashing import content_hash
            vault_id = content_hash(analysis)
            vault_path = os.path.join("tmp", f"vault-{vault_id}.txt")
            abs_vault_path = os.path.abspath(vault_path)
            os.makedirs(os.path.dirname(abs_vault_path), exist_ok=True)
            with open(abs_vault_path, "w") as f:
                f.write(analysis)
            logger.info(f"[EXPERT] Vaulted large response to {vault_path}")
            return f"§§include({abs_vault_path})"
            
        return analysis
            
    except Exception as e:
        logger.warning(f"LLM analysis failed: {e}, using fallback template")
    
    # Fallback template
    return _generate_fallback_template(issue_data, context)


def _render_expert_template(
    data: Dict[str, Any], 
    issue_num: str, 
    issue_title: str, 
    issue_body: str, 
    context: str,
    research_data: str = ""
) -> str:
    """Render the expert analysis template with LLM data."""
    components = ", ".join(data.get("components_affected", ["[Analysis pending]"]))
    
    # Build implementation roadmap table
    phases = data.get("implementation_phases", [])
    roadmap_rows = []
    for p in phases[:5]:
        affected_files = ", ".join(p.get("files", []))
        roadmap_rows.append(f"| {p.get('phase', 'TBD')} | {p.get('action', 'TBD')} | `{affected_files}` | {p.get('rationale', 'TBD')} |")
    roadmap_table = "\n".join(roadmap_rows) if roadmap_rows else "| Setup | Create tests | `tests/` | TDD approach |"
    
    # Build risks table
    risks = data.get("risks", [])
    risk_rows = []
    for r in risks[:4]:
        risk_rows.append(f"| {r.get('risk', 'TBD')} | {r.get('impact', 'Med')} | {r.get('mitigation', 'TBD')} |")
    risks_table = "\n".join(risk_rows) if risk_rows else "| Unknown Risk | Med | Review required |"
    
    # Build edge cases
    edge_cases = data.get("edge_cases", ["Edge case 1", "Edge case 2", "Edge case 3"])
    edge_case_list = "\n".join([f"- **Edge Case {i+1}**: {ec}" for i, ec in enumerate(edge_cases[:3])])
    
    # Build sources
    sources = data.get("sources", [])
    sources_list = "\n".join([f"- [{i+1}] {s}" for i, s in enumerate(sources[:5])]) if sources else "- [1] Issue description and codebase context"
    
    # Build question answer section if present
    question_answer = data.get('question_answer', '')
    question_section = ""
    if question_answer and question_answer.strip() and question_answer != "<If the description contains questions...>":
        question_section = f"""## 💬 Direct Answer

{question_answer}

"""
    
    # Build research section if available
    research_section = ""
    if research_data and len(research_data) > 50:
        research_section = f"""## 📊 Market & Research Data

> *Research powered by Perplexity AI*

{truncate_output_middle_out(research_data, max_chars=3000, head_ratio=0.3)}

"""
    manual_test_plan = data.get('manual_test_plan') or '1. Open the application.\n2. Verify the changes manually.'
    
    return f"""{data.get('summary', 'Technical analysis of the issue.')}
{question_section}{research_section}
## 🎯 Product & Business Value

- **Business Impact**: {data.get('business_value', 'Improves user experience and system capabilities.')}
- **User Impact**: {data.get('user_impact', 'Enhanced workflow and reduced friction.')}
- **Priority Rationale**: {data.get('priority_rationale', 'Aligns with roadmap and user requests.')}

## 🏗️ Architectural Assessment

- **Core Components Affected**: {components}
- **Technical Strategy**: {data.get('technical_strategy', 'TDD-driven implementation approach.')}
- **Issue Type**: {data.get('issue_type', 'feature').upper()}
- **Specialized Analysis**: {data.get('specialized_analysis', 'Further analysis recommended.')}

```mermaid
{data.get('mermaid_diagram', 'graph TD' + chr(10) + '    A["Issue"] --> B["Analysis"]' + chr(10) + '    B --> C["Implementation"]')}
```

## 🎯 Implementation Roadmap

| Phase | Action | Targeted Files | Rationale |
|-------|--------|----------------|-----------|
{roadmap_table}

## 💻 Code Pattern / Strategy

```{data.get('code_language', 'python')}
{data.get('code_pattern', '# Implementation pattern TBD')}
```

## ![WARNING] Risk & Mitigation Matrix

| Risk | Impact | Mitigation |
|------|--------|------------|
{risks_table}

## ✅ Verification Strategy

- **Core Unit Tests**: Validate primary functionality with TDD
{edge_case_list}
- **Manual Test Plan (Human Step-by-Step)**:
{manual_test_plan}
- **Regression Prevention**: {data.get('regression_prevention', 'Integration tests and CI/CD validation')}

## Sources
{sources_list}
"""


def _generate_fallback_template(issue_data: Dict[str, Any], context: str) -> str:
    """Generate fallback template when LLM analysis fails."""
    issue_num = issue_data.get('number', 'N/A')
    issue_title = issue_data.get('title', 'Unknown')
    issue_body = issue_data.get('body', 'No description')[:800]
    
    return f"""# 🎯 Expert Solution Analysis: #{issue_num}

> **{issue_title}**

{issue_body}

---

## 🏗️ Architectural Assessment

- **Core Components Affected**: *[Gap: Codebase analysis incomplete - manual review recommended]*
- **Technical Strategy**: Based on the issue description, this requires implementation following TDD principles.
- **Type Analysis**:
    - *If Bug*: Root cause investigation needed with debug logging.
    - *If Feature*: Architecture alignment review recommended.
    - *If Performance*: Profiling and benchmarking required.

```mermaid
graph TD
    A["Issue #{issue_num}"] --> B["Analysis Required"]
    B --> C["Implementation"]
    C --> D["Testing"]
    D --> E["Deployment"]
```

## 🎯 Implementation Roadmap

| Phase | Action | Targeted Files | Rationale |
|-------|--------|----------------|-----------|
| **Setup** | Create failing tests | `tests/` | TDD approach |
| **Implement** | Core logic changes | *[Gap: Files TBD]* | Address requirements |
| **Verify** | Run test suite | `tests/` | Ensure correctness |
| **Deploy** | Merge and release | - | Ship to production |

## 💻 Code Pattern Suggestion

*[Gap: Specific code patterns require deeper codebase analysis]*

General approach:
- Follow existing project conventions
- Add comprehensive error handling
- Include logging for debugging

## ![WARNING] Risk & Mitigation Matrix

| Risk | Impact | Mitigation |
|------|--------|------------|
| Incomplete requirements | Medium | Clarify with stakeholders |
| Regression in existing features | High | Comprehensive test coverage |
| Performance impact | Medium | Benchmark before/after |

## ✅ Verification Strategy

- **Core Unit Tests**: Test all new/modified functions
- **Edge Case 1**: Empty/null input handling
- **Edge Case 2**: Concurrent access scenarios
- **Edge Case 3**: Error condition recovery
- **Regression Prevention**: Run full test suite before merge

---

*Note: This analysis was generated with limited codebase context. A deeper manual review is recommended for complex implementations.*

## Codebase Context (Partial)

{context if context else '*[Gap: No codebase context available]*'}
"""


async def get_codebase_context(path: str) -> str:
    """
    Get codebase summary using 'gitingest' or a Python fallback.
    Returns the raw string content, truncated to MAX_CODEBASE_WORDS.
    """
    gitingest_bin = shutil.which("gitingest") or "/opt/homebrew/bin/gitingest"
    
    cmd = None
    if os.path.exists(gitingest_bin):
        cmd = [gitingest_bin, path]
        for pat in DEFAULT_EXCLUDES:
            cmd.extend(["-e", pat])
        
        logger.info(f"Using gitingest: {' '.join(cmd)}")
    
    stdout = ""
    if cmd:
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, _ = process.communicate(timeout=120)
            if process.returncode != 0:
                stdout = ""
        except Exception as e:
            logger.warning(f"Gitingest failed: {e}")
            stdout = ""

    # Fallback if gitingest failed or not found
    if not stdout:
        stdout = await get_codebase_context_fallback(path, DEFAULT_EXCLUDES)

    # Enforce Word Limit
    words = stdout.split()
    if len(words) > MAX_CODEBASE_WORDS:
        logger.info(f"Truncating codebase digest from {len(words)} to {MAX_CODEBASE_WORDS} words")
        stdout = " ".join(words[:MAX_CODEBASE_WORDS]) + "\n\n... [TRUNCATED DUE TO 125K WORD LIMIT] ..."

    # Persistent Digest: Save to project root if possible
    try:
        digest_path = os.path.join(path, "digest.txt")
        with open(digest_path, "w") as f:
            f.write(stdout)
        logger.info(f"Saved codebase digest to {digest_path}")
    except Exception as e:
        logger.debug(f"Could not save digest.txt to {path}: {e}")

    return stdout


async def get_codebase_context_fallback(path: str, ignore_patterns: List[str] = None) -> str:
    """Simple python fallback for codebase context extraction."""
    if ignore_patterns is None:
        ignore_patterns = DEFAULT_EXCLUDES

    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"Error: Path {path} not found."
        
    output = [f"# Codebase Summary: {path} (Fallback)"]
    count = 0
    
    # Compile ignore patterns for faster checking
    dir_ignores = set()
    file_ignores = []
    for pat in ignore_patterns:
        if '*' in pat or '?' in pat:
            file_ignores.append(pat.replace('.', '\\.').replace('*', '.*'))
        else:
            dir_ignores.add(pat)

    for root, dirs, files_list in os.walk(abs_path):
        dirs[:] = [d for d in dirs if d not in dir_ignores and not d.startswith('.')]
        
        for f in files_list:
            if count > 150:
                break
            
            should_ignore = False
            for pat in file_ignores:
                if re.match(pat, f):
                    should_ignore = True
                    break
            if should_ignore:
                continue

            f_path = os.path.join(root, f)
            rel = os.path.relpath(f_path, abs_path)
            
            output.append(f"### `{rel}`")
            output.append("```")
            if any(f.endswith(pat.replace('*', '')) for pat in DEFAULT_EXCLUDES if pat.startswith('*.')):
                if f not in ROOT_INCLUDES:
                    continue
            try:
                with open(f_path, 'r', errors='ignore') as f_in:
                    content = f_in.read(4000)
                    output.append(content if content else "[Empty file]")
            except Exception as e:
                output.append(f"[Could not read file: {e}]")
            output.append("```\n")
            count += 1
        if count > 150:
            break
    return "\n".join(output)