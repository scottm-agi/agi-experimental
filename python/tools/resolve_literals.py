from __future__ import annotations
"""
Resolve Literals Tool — Volatile Fact Resolution
===================================================
Resolves human-readable service/model/API references from prompt
requirements into current, verified technical identifiers.

This tool addresses the class of errors where agents use stale training-data
knowledge for volatile facts (model IDs, env var names, SDK versions).

Architecture:
  Layer 1: Researcher-grounded facts (docs/framework-research.md)
  Layer 2: Content manifest cross-reference (content_manifest.json)
  Layer 3: Model resolver (resolve_model_slug from model_resolver.py)
  Layer 4: Web search fallback (Perplexity/Tavily)  — future

RCA Reference: RCA-235 Issues 2, 4, 5
Design Doc: agix-devdocs/docs/architecture/literals_resolution_tool.md
"""

import os
import re
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from python.helpers.tool import Tool, Response

# RCA-15 F2-c: Import model_resolver for Layer 3
try:
    from python.helpers.model_resolver import resolve_model_slug
except ImportError:
    resolve_model_slug = None  # Graceful degradation

logger = logging.getLogger("agix.resolve_literals")


# ─── Service Pattern Extractors ──────────────────────────────────────

# Regex patterns for extracting structured data from researcher markdown
_ENV_VAR_RE = re.compile(r"Env\s*var[s]?:\s*`([A-Z_][A-Z0-9_]+)`", re.IGNORECASE)
_SDK_RE = re.compile(r"SDK:\s*`([^`]+)`", re.IGNORECASE)
_VERSION_RE = re.compile(r"version\s+([^\s,]+)", re.IGNORECASE)
_IMPORT_RE = re.compile(r"Import:\s*`([^`]+)`", re.IGNORECASE)
_MODEL_ID_RE = re.compile(r"Model\s*ID:\s*`([^`]+)`", re.IGNORECASE)
_BASE_URL_RE = re.compile(r"Base\s*URL:\s*`?([^\s`]+)`?", re.IGNORECASE)
_DOCS_URL_RE = re.compile(r"Docs:\s*`?([^\s`]+)`?", re.IGNORECASE)


class ResolveLiterals(Tool):
    """
    Resolves human-readable service/model/API references from prompt
    requirements into current, verified technical identifiers.

    Available to ALL agents. Should be called BEFORE writing any code
    that references an external service.

    Args:
        service: str — Human-readable name ("Resend", "Claude Sonnet 4 via OpenRouter")
        category: str — One of: llm_model, email_provider, payment_provider,
                        auth_provider, database, design, general
        context: str — Optional context ("for transactional email", etc.)
        project_path: str — Path to project root (auto-detected if not provided)
    """

    async def execute(self, **kwargs) -> Response:
        service = self.args.get("service", "")
        category = self.args.get("category", "general")
        context = self.args.get("context", "")

        if not service:
            return Response(
                message=(
                    "Error: 'service' argument is required. Provide the human-readable "
                    "service name to resolve (e.g., 'Resend', 'Claude Sonnet 4 via OpenRouter')."
                ),
                break_loop=False,
            )

        # Determine project path
        project_path = self.args.get("project_path", "")
        if not project_path:
            project_path = self._detect_project_path()

        now = datetime.now(timezone.utc)
        resolved: Dict[str, Any] = {}
        resolution_source = "none"

        # ── Layer 1: Researcher-grounded facts ───────────────────
        researcher_data = self._resolve_from_researcher(service, category, project_path)
        if researcher_data:
            resolved.update(researcher_data)
            resolution_source = "researcher_output"
            logger.info(f"[LITERALS] Resolved '{service}' from researcher output")

        # ── Layer 2: Content manifest cross-reference ────────────
        manifest_data = self._resolve_from_manifest(service, category, project_path)
        if manifest_data:
            # Manifest supplements but doesn't override researcher
            for k, v in manifest_data.items():
                if k not in resolved:
                    resolved[k] = v
            if resolution_source == "none":
                resolution_source = "content_manifest"
                logger.info(f"[LITERALS] Resolved '{service}' from content manifest")

        # ── Layer 3: Model resolver (RCA-15 F2-c) ────────────────
        if not resolved:
            model_data = self._resolve_from_model_resolver(service, category)
            if model_data:
                resolved.update(model_data)
                resolution_source = "model_resolver"
                logger.info(f"[LITERALS] Resolved '{service}' from model_resolver")

        # ── Layer 4: Fallback instruction ─────────────────────────
        if not resolved:
            resolution_source = "fallback_instruction"
            logger.info(f"[LITERALS] No local data for '{service}' — providing search instructions")
            return self._build_fallback_response(service, category, context, now)

        # ── Build structured response ────────────────────────────
        return self._build_resolved_response(
            service, category, resolved, resolution_source, now
        )

    # ─── Layer 1: Researcher Output ───────────────────────────────

    def _resolve_from_researcher(
        self, service: str, category: str, project_path: str
    ) -> Optional[Dict[str, Any]]:
        """Parse docs/framework-research.md for pre-verified service data."""
        research_path = os.path.join(project_path, "docs", "framework-research.md")
        if not os.path.isfile(research_path):
            return None

        try:
            with open(research_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (IOError, OSError) as e:
            logger.warning(f"[LITERALS] Failed to read researcher output: {e}")
            return None

        # Find the section matching this service
        section = self._find_service_section(content, service)
        if not section:
            return None

        resolved = {}

        # Extract env var(s)
        env_vars = _ENV_VAR_RE.findall(section)
        if env_vars:
            resolved["env_var"] = env_vars[0]
            if len(env_vars) > 1:
                resolved["env_vars"] = env_vars

        # Extract SDK package
        sdk_match = _SDK_RE.search(section)
        if sdk_match:
            resolved["sdk_package"] = sdk_match.group(1).strip()

        # Extract version
        version_match = _VERSION_RE.search(section)
        if version_match:
            resolved["sdk_version"] = version_match.group(1).strip()

        # Extract import pattern
        import_matches = _IMPORT_RE.findall(section)
        if import_matches:
            # Determine TS vs PY import
            for imp in import_matches:
                if "import" in imp.lower() and "from" in imp.lower():
                    resolved["sdk_import_ts"] = imp
                elif "import " in imp:
                    resolved["sdk_import_ts"] = imp

        # Extract model ID (for LLM category)
        model_match = _MODEL_ID_RE.search(section)
        if model_match:
            resolved["model_id"] = model_match.group(1).strip()

        # Extract base URL
        base_url_match = _BASE_URL_RE.search(section)
        if base_url_match:
            resolved["base_url"] = base_url_match.group(1).strip()

        # Extract docs URL
        docs_match = _DOCS_URL_RE.search(section)
        if docs_match:
            resolved["docs_url"] = docs_match.group(1).strip()

        return resolved if resolved else None

    def _find_service_section(self, content: str, service: str) -> Optional[str]:
        """Find the markdown section that discusses a given service."""
        service_lower = service.lower()

        # Extract individual service names from compound references
        # e.g., "Claude Sonnet 4 via OpenRouter" → look for both
        search_terms = [service_lower]

        # Handle "X via Y" pattern
        via_match = re.match(r"(.+?)\s+via\s+(.+)", service, re.IGNORECASE)
        if via_match:
            search_terms.extend([
                via_match.group(1).strip().lower(),
                via_match.group(2).strip().lower(),
            ])

        # Split content into sections by ## headers
        sections = re.split(r"(?=^##\s)", content, flags=re.MULTILINE)

        for section in sections:
            section_lower = section.lower()
            for term in search_terms:
                # Match service name in section header or body
                if term in section_lower:
                    return section

        return None

    # ─── Layer 2: Content Manifest ────────────────────────────────

    def _resolve_from_manifest(
        self, service: str, category: str, project_path: str
    ) -> Optional[Dict[str, Any]]:
        """Read content-manifest.json for service-specific constraints."""
        # RCA-457: Check both hyphen (canonical) and underscore (legacy) paths
        manifest_path = None
        for candidate in [
            os.path.join(project_path, "docs", "content-manifest.json"),
            os.path.join(project_path, "content_manifest.json"),
            os.path.join(project_path, "content-manifest.json"),
        ]:
            if os.path.isfile(candidate):
                manifest_path = candidate
                break
        if not manifest_path:
            return None

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (IOError, OSError, json.JSONDecodeError) as e:
            logger.warning(f"[LITERALS] Failed to read manifest: {e}")
            return None

        resolved = {}
        service_lower = service.lower()

        # Handle design category — extract colors/mode from manifest branding
        if category == "design" or "color" in service_lower or "dark" in service_lower or "mode" in service_lower:
            branding = manifest.get("branding", {})
            colors = branding.get("colors", {})
            if colors:
                for key, value in colors.items():
                    resolved[key] = value
            mode = branding.get("mode")
            if mode:
                resolved["color_scheme"] = mode

        # Handle integration categories — normalize dict/list to list (System 5 / ADR-82)
        integrations = manifest.get("integrations", [])
        if isinstance(integrations, dict):
            # LEGACY: dict shape → normalize to list of dicts
            integrations_list = [
                {"name": k, **(v if isinstance(v, dict) else {"value": v})}
                for k, v in integrations.items()
            ]
        elif isinstance(integrations, list):
            integrations_list = integrations
        else:
            integrations_list = []
        for integration in integrations_list:
            if not isinstance(integration, dict):
                continue
            int_key = integration.get("name", "")
            provider = integration.get("provider", int_key)
            if provider.lower() in service_lower or service_lower in provider.lower():
                # Copy all integration data except 'name' and 'provider'
                for k, v in integration.items():
                    if k not in ("name", "provider"):
                        resolved[k] = v

        return resolved if resolved else None

    # ─── Layer 3: Model Resolver (RCA-15 F2-c) ───────────────────

    def _resolve_from_model_resolver(
        self, service: str, category: str
    ) -> Optional[Dict[str, Any]]:
        """Resolve model marketing names to API slugs via model_resolver.

        RCA-15 RC-2: When Layers 1+2 returned nothing for model slugs,
        the tool returned a fallback instruction that the code agent ignored,
        causing it to use stale training data. This layer calls
        resolve_model_slug() which has a catalog + static fallback mapping
        for well-known models.

        Only activates for 'llm_model' category to avoid false matches.
        """
        if category != "llm_model":
            return None

        try:
            if resolve_model_slug is None:
                logger.warning("[LITERALS] model_resolver not available — skipping Layer 3")
                return None

            slug = resolve_model_slug(service)
            if slug:
                logger.info(f"[LITERALS] Layer 3: model_resolver resolved '{service}' → '{slug}'")
                return {"model_id": slug}
        except Exception as e:
            logger.warning(f"[LITERALS] Layer 3 model_resolver error: {e}")

        return None

    # ─── Fallback Response ────────────────────────────────────────

    def _build_fallback_response(
        self, service: str, category: str, context: str, now: datetime
    ) -> Response:
        """Build instruction for agent to search and verify the service."""
        search_guidance = {
            "llm_model": (
                f"Search for the current model identifier for '{service}'. "
                f"Check the provider's official API documentation or model listing. "
                f"For OpenRouter, query https://openrouter.ai/api/v1/models and find the matching model_id."
            ),
            "email_provider": (
                f"Search for '{service}' official documentation. "
                f"Find the correct environment variable name and SDK import pattern. "
                f"Check the official quickstart guide for the standard env var convention."
            ),
            "payment_provider": (
                f"Search for '{service}' official API documentation. "
                f"Find the correct server-side secret key env var name and SDK package. "
                f"Verify against the official integration guide."
            ),
            "design": (
                f"The design system for this project could not be resolved from the manifest. "
                f"Check the original prompt for explicit color values, mode (dark/light), "
                f"and typography specifications. Use prompt-specified values directly."
            ),
        }

        guidance = search_guidance.get(category, (
            f"Search for '{service}' official documentation to verify the correct "
            f"technical identifiers (env vars, SDK packages, import patterns). "
            f"Do NOT use training data — verify against current official docs."
        ))

        if context:
            guidance += f"\nAdditional context: {context}"

        return Response(
            message=(
                f"**Literal Resolution — Fallback**\n\n"
                f"No pre-verified data found for **{service}** (category: {category}).\n\n"
                f"**Action Required**: {guidance}\n\n"
                f"⚠️ Do NOT guess from training data. Verify against current sources.\n\n"
                f"_Checked at: {now.isoformat()}_"
            ),
            break_loop=False,
        )

    # ─── Resolved Response ────────────────────────────────────────

    def _build_resolved_response(
        self,
        service: str,
        category: str,
        resolved: Dict[str, Any],
        resolution_source: str,
        now: datetime,
    ) -> Response:
        """Build structured response with resolved literals."""
        # Format resolved data as a readable block
        resolved_lines = []
        for key, value in resolved.items():
            resolved_lines.append(f"  - **{key}**: `{value}`")

        resolved_block = "\n".join(resolved_lines)

        # Build JSON block for programmatic consumption
        json_payload = json.dumps(
            {
                "service": service,
                "category": category,
                "resolved": resolved,
                "resolution_source": resolution_source,
                "verified_at": now.isoformat(),
            },
            indent=2,
        )

        return Response(
            message=(
                f"**Literal Resolution — {service}**\n\n"
                f"Resolved from: **{resolution_source}**\n\n"
                f"**Resolved values**:\n{resolved_block}\n\n"
                f"```json\n{json_payload}\n```\n\n"
                f"Use these values directly in your code. Do NOT substitute "
                f"with training-data guesses."
            ),
            break_loop=False,
        )

    # ─── Helpers ──────────────────────────────────────────────────

    def _detect_project_path(self) -> str:
        """Try to detect the project path from agent context."""
        try:
            # Try agent data first
            project_path = self.agent.get_data("project_path")
            if project_path:
                return str(project_path)
        except Exception:
            pass

        try:
            # Try to find from config
            if hasattr(self.agent, "config") and hasattr(self.agent.config, "project_dir"):
                return str(self.agent.config.project_dir)
        except Exception:
            pass

        # Fallback to CWD
        return os.getcwd()
