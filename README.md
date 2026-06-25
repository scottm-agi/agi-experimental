# agi-experimental: Autonomous Multi-Agent Intelligence Framework

> **⚠️ Experimental — Expect Bugs**
>
> This is a fork of [Agent Zero](https://github.com/frdel/agent-zero) from ~6 months ago, with approximately **2 million lines of code** built on top. Key architectural patterns from [RooCode](https://github.com/RooVetGit/Roo-Code) were "translated" into this framework to make the system proficient in software development workflows.
>
> Some component architectures are still evolving and should be treated as experimental. The AGI system is currently missing key navigation components needed to complete the observer loop.

---

## What is agi-experimental?

An autonomous framework for multi-agent orchestration and repository automation. It transforms sequential agent behaviors into parallel collaborative swarms, enabling complex task resolution at scale.

The system features:
- **Dynamic agent creation** — agents can self-instantiate specialized sub-agents
- **Parallel execution** — wave-based task decomposition with dependency analysis
- **Full-stack development** — BDD → TDD → Code pipeline with quality gates
- **Repository automation** — GitHub/Forgejo issue-to-PR workflows
- **Self-healing infrastructure** — circuit breakers, crash recovery, database repair

## Quick Start

### Prerequisites

- **Docker** (v20.10+) and **Docker Compose** (v2.0+)
- An API key for at least one supported LLM provider

### Installation

```bash
# Clone the repository
git clone <repo-url> agi-experimental && cd agi-experimental

# Copy environment template and configure
cp .env.example .env
# Edit .env with your API keys and settings

# Start the application
docker compose -f docker-compose.dev.yml up -d
```

The web UI will be available at `http://localhost:50001` (or your configured `WEB_UI_PORT`).

## Core Features

### 1. Dynamic Agentic Intelligence
- **Multiple Agent Swarms**: Coordinate distinct swarms of agents simultaneously to tackle multi-faceted objectives in parallel.
- **Domain Specific Agent Swarms**: Deploy tailored swarms of agents specialized for specific industries or complex domains.
- **Any Model Support**: Native integration with OpenAI, Anthropic, AWS Bedrock, Google Gemini, xAI (Grok), and secondary providers via LiteLLM.
- **Adaptive Orchestration**: Dynamic switching between sequential, parallel, wave, and adaptive execution modes based on task complexity.
- **Dynamic Agent Creation**: Agents can self-instantiate specialized sub-agents with custom personas and toolsets to solve novel problems.
- **Profile System**: Configurable agent profiles (Orchestrator, Code Agent, Browser Agent, etc.) with tool and behavior specialization.

### 2. Skills & Capabilities
- **Skill Engine**: A modular system for importing and creating specialized capabilities (e.g., UI/UX design, security auditing, data engineering).
- **MCP Integration**: Native support for the Model Context Protocol (MCP), allowing connection to external tools, databases, and services.
- **124+ Built-in Tools**: Code execution, file management, git operations, browser automation, search, image generation, and more.
- **PowerPoint & Media Toolkit**: Document and presentation generation via Node.js/Playwright helpers.

### 3. Development Pipeline
- **Requirements Extraction**: Automated parsing of user prompts into structured requirements with acceptance criteria.
- **BDD Generation**: Behavioral specifications generated from requirements before any code is written.
- **TDD Pipeline**: Test-driven development with red-green-refactor cycle enforcement.
- **Quality Gates**: Multi-phase verification including integration checks, content regression guards, and build verification.
- **Design Spec System**: DTCG-compatible design tokens extracted from requirements for consistent UI implementation.

### 4. Enterprise Scale & Performance
- **In-Memory Performance**: Tiered memory architecture utilizing Redis Streams for collaboration and Milvus for vector similarity search.
- **Parallel Swarms**: Wave-based execution with dependency analysis, allowing up to 50+ concurrent task operations without memory exhaustion.
- **Hybrid Persistence**: Dual-engine architecture offloading high-volume telemetry to PostgreSQL 17 while maintaining core state in SQLite for portability.
- **Budget Management**: Token tracking, cost modeling, and budget reserves to prevent runaway spending.

### 5. Repository Automation
- **GitHub/Forgejo Integration**: Automated issue triage, analysis, solution development, and PR creation.
- **Webhook-Driven**: Event-driven architecture responding to issues, comments, and PRs.
- **Multi-Repo Support**: Manage multiple repositories with project isolation and configuration.

### 6. Observability & Reliability
- **Agent Tracing**: Full execution traces with HTML reports for debugging agent behavior.
- **Self-Healing Databases**: Repair mechanisms that intercept SQLite corruption before connection, with automatic archiving and state restoration.
- **Autonomous Monitoring**: Supervisor agents that proactively monitor task execution, detect loops, and intervene to optimize success rates.
- **Circuit Breakers**: Automatic failure detection and recovery for external service integrations.

## Architecture

The framework operates within an isolated container environment:

- **Isolated Execution**: All code, tools, and agents run within a multi-tier Docker environment, ensuring absolute isolation from the host system.
- **Hardened Git**: Multi-level defense-in-depth including `GIT_CEILING_DIRECTORIES`, mandatory git shims, and physical path resolution to prevent repository traversal escapes.
- **Resource Management**: Semaphore-controlled worker pools and thread-safe tool registries to maintain stability under parallel load.

## Project Structure

| Directory | Description |
|-----------|-------------|
| `python/` | Core agent framework, tools, helpers, and API handlers |
| `python/helpers/` | ~405 helper modules — the engine of the framework |
| `python/tools/` | ~124 tool implementations |
| `python/extensions/` | ~69 lifecycle extensions |
| `python/api/` | REST API endpoints |
| `webui/` | Frontend web interface (Alpine.js + vanilla CSS) |
| `prompts/` | System and tool prompt templates (~213 files) |
| `docker/` | Docker build and runtime configuration |
| `scripts/` | Admin and management scripts |
| `skills/` | Modular agent skills |
| `conf/` | Configuration files (model providers, parallel config, etc.) |


## Security & Privacy

- **ZDR (Zero Data Retention)**: Settings for and default enforcement of ZDR flags for sensitive LLM calls to ensure user data is never used for training.
- **Telemetry Protection**: Telemetry off by default for new packages.
- **PII & Secret Protection**: Automated obfuscation and character sequence expansion for sensitive credentials.
- **Privacy Mode**: Global toggle injecting anti-logging and opt-out flags into all supported LLM providers.

## Extension Development

Extensions hook into the agent lifecycle at defined extension points:

```python
# python/extensions/_XX_my_extension.py
from python.helpers.extension import Extension

class MyExtension(Extension):
    async def execute(self, **kwargs):
        # Your logic here
        pass
```

Key extension points: `agent_init`, `message_loop_start`, `message_loop_prompts_after`, `tool_execute_before/after`, `monologue_start/end`.

## Tool Development

Tools are auto-discovered from `python/tools/` and require a corresponding prompt at `prompts/agent.system.tool.<tool_name>.md`.

```python
# python/tools/my_tool.py
from python.helpers.tool import Tool, Response

class MyTool(Tool):
    async def execute(self, **kwargs) -> Response:
        return Response(message="Result", break_loop=False)
```
## Origin & Acknowledgments

This project is a fork of [Agent Zero](https://github.com/frdel/agent-zero) by Jan Tomasek, extended with:
- Multi-agent parallel orchestration (wave-based swarms)
- Full-stack development pipeline (BDD → TDD → Code)
- Repository automation (GitHub/Forgejo integration)
- Enterprise observability (tracing, circuit breakers, supervisor agents)
- 400+ helper modules and 120+ tools
- Architectural patterns adapted from [RooCode](https://github.com/RooVetGit/Roo-Code)

## License

[MIT-0 (No Attribution Required)](LICENSE)
