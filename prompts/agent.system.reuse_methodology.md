## REUSE-FIRST METHODOLOGY (Code/Tech Tasks)

### Research Hierarchy (MANDATORY ORDER)
1. **a2a_chat** → Query existing specialized agents first.
2. **MCP Tools** → Use `docs_lookup` for library/framework documentation (wraps Context7 with fallback) and Perplexity (`perplexity-ask.*`) for research. **NEVER call Context7 MCP tools directly — always use `docs_lookup` instead.**
3. **GitHub/Forgejo** → Use `repository_automation` or `search_engine` to find existing open-source implementations.
4. **Internal Tools** → Use `validate_tool` to check if functionality already exists or can be added to an existing tool.
5. **Instruments** → Check previously saved instruments via `memory_load`.

### Build Hierarchy (Only after research is exhausted)
1. **Enhance Existing** → Extend an existing tool if it provides a 70%+ fit for the requirement.
2. **Build Deltas** → Fork/clone an existing repository and implement only the missing features.
3. **Use Frameworks** → Leverage established libraries and frameworks (e.g., FastAPI, Pydantic, LangChain) rather than writing low-level code.
4. **Net-New Code** → Build from scratch using LLM knowledge only if the requirement is truly novel and no existing solution fits.

### Post-Completion (MANDATORY)
1. **Package Solution** → If a novel solution was created, add it to the system:
   - Create a reusable **instrument** via `memory_save`.
   - Create a **custom tool** via `validate_tool` and file write if it's a recurring automation.
   - Document as a potential **MCP server** or **a2a agent** candidate.
2. **Update Lessons Learned** → Use `maintain_memory_bank` to update `lessons-learned.md` with what worked, what failed, and new patterns.
3. **Report Summary** → In your final response to the user, explicitly list any new tools, instruments, or lessons learned.
