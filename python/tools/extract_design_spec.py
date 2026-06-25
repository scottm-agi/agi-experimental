"""extract_design_spec — Vision LLM structured design extraction tool (ADR-83).

Takes mockup images (from generate_image or manual upload) and uses Vision LLM
to extract structured design data. Writes 4 deterministic output files:

1. design-tokens.json     — W3C DTCG format (primitive + semantic tiers)
2. component-spec.json    — Component tree with typed props + slots
3. section-map.json       — Page section roles (hero, pricing, etc.)
4. design-brief.md        — LLM-ready system prompt for code agent

Input resolution (checked in order):
1. image_paths  — explicit list of image files
2. image_dir    — directory to scan for all images
3. Auto-discover — scan project dir's canonical locations

All Vision LLM calls are isolated in _call_vision_llm() for testability.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import os
from typing import Any, Optional

from python.helpers.tool import Tool, Response
from python.helpers.print_style import PrintStyle

logger = logging.getLogger("extract-design-spec")

# ── Image file extensions to discover ──
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# ── Canonical directories to auto-discover mockups from ──
_AUTO_DISCOVER_DIRS = [
    "docs/design-mockups",
    "docs/mockups",
    "docs/brand",
    "design-kit",
    "mockups",
]

# ── Structured extraction prompt for Vision LLM ──
EXTRACTION_PROMPT = """Analyze this UI mockup image and extract a structured design specification.

Return a JSON object with EXACTLY this structure:

{
  "colors": {
    "primary": "#HEX",
    "secondary": "#HEX or null",
    "accent": "#HEX or null",
    "background": "#HEX",
    "surface": "#HEX",
    "text": { "primary": "#HEX", "secondary": "#HEX", "muted": "#HEX" },
    "neutrals": ["#HEX", ...],
    "gradients": ["css-gradient-string", ...]
  },
  "typography": {
    "headingFamily": "Font Name",
    "bodyFamily": "Font Name",
    "scale": [
      { "role": "h1", "size": "48px", "weight": "700", "lineHeight": "1.2" },
      { "role": "h2", "size": "36px", "weight": "600", "lineHeight": "1.3" },
      { "role": "body", "size": "16px", "weight": "400", "lineHeight": "1.5" },
      { "role": "caption", "size": "12px", "weight": "400", "lineHeight": "1.4" }
    ]
  },
  "spacing": {
    "scale": [4, 8, 12, 16, 24, 32, 48, 64, 96],
    "sectionGap": "64px",
    "componentGap": "24px"
  },
  "borders": {
    "radii": [4, 8, 12, 16, 9999],
    "widths": [1, 2]
  },
  "shadows": [
    { "name": "sm", "value": "0 1px 2px rgba(0,0,0,0.05)" },
    { "name": "md", "value": "0 4px 6px rgba(0,0,0,0.1)" }
  ],
  "sections": [
    {
      "role": "hero|feature-grid|pricing|testimonial|cta|faq|footer|nav|stats|steps",
      "heading": "Actual heading text from mockup",
      "subheading": "Actual subheading text",
      "ctaText": "Button text if any",
      "ctaAction": "URL or action description",
      "components": ["component-kind-1", "component-kind-2"]
    }
  ],
  "components": [
    {
      "kind": "button|card|input|nav|badge|avatar|table|...",
      "variants": ["primary", "secondary", "ghost"],
      "sizes": ["sm", "md", "lg"],
      "slots": { "label": true, "icon": true, "badge": false },
      "sampleText": ["actual button text from mockup"]
    }
  ],
  "voice": {
    "tone": "professional|casual|friendly|authoritative|playful",
    "ctaVerbs": ["Start", "Get", "Try", "Book"],
    "headingStyle": "question|statement|action|benefit",
    "pronoun": "you|we|they"
  }
}

CRITICAL RULES:
1. Extract ACTUAL text from the mockup — never invent placeholder text
2. Colors must be hex codes — estimate from the visual appearance
3. Every section visible in the mockup must appear in sections[]
4. Components must include ALL visual variants visible in the mockup
5. Typography scale must include sizes for ALL heading levels visible
6. Return ONLY the JSON object — no markdown fences, no explanation text
"""


# ── Default structures for normalization ──

_DEFAULT_COLORS = {
    "primary": "#000000",
    "secondary": None,
    "accent": None,
    "background": "#FFFFFF",
    "surface": "#F5F5F5",
    "text": {"primary": "#000000", "secondary": "#666666", "muted": "#999999"},
    "neutrals": [],
    "gradients": [],
}

_DEFAULT_TYPOGRAPHY = {
    "headingFamily": "sans-serif",
    "bodyFamily": "sans-serif",
    "scale": [],
}

_DEFAULT_SPACING = {
    "scale": [4, 8, 16, 24, 32, 48, 64],
}

_DEFAULT_BORDERS = {
    "radii": [4, 8],
    "widths": [1],
}

_DEFAULT_VOICE = {
    "tone": "professional",
    "ctaVerbs": [],
    "headingStyle": "statement",
    "pronoun": "you",
}


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC FUNCTIONS — testable standalone, also used by the Tool class
# ═══════════════════════════════════════════════════════════════════════


def discover_images(directory: str) -> list[str]:
    """Discover image files in a directory (recursive), sorted by filename.

    Args:
        directory: Absolute path to scan for images.

    Returns:
        Sorted list of absolute paths to image files.
        Empty list if directory doesn't exist or contains no images.
    """
    if not directory or not os.path.isdir(directory):
        return []

    images = []
    for root, _dirs, filenames in os.walk(directory):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in _IMAGE_EXTENSIONS:
                images.append(os.path.join(root, fname))

    # Sort by basename so 00-design-system comes before 01-homepage
    images.sort(key=lambda p: os.path.basename(p))
    return images


def normalize_vision_response(raw: dict) -> dict:
    """Normalize a Vision LLM response, filling defaults for missing fields.

    Args:
        raw: The raw JSON dict from Vision LLM.

    Returns:
        A normalized design dict with all required keys present.
    """
    result = {}

    # Colors — deep merge with defaults
    raw_colors = raw.get("colors", {})
    result["colors"] = {**_DEFAULT_COLORS}
    if isinstance(raw_colors, dict):
        for key, val in raw_colors.items():
            if val is not None:
                result["colors"][key] = val
        # Ensure text sub-dict has defaults
        raw_text = raw_colors.get("text", {})
        default_text = _DEFAULT_COLORS["text"].copy()
        if isinstance(raw_text, dict):
            default_text.update(raw_text)
        result["colors"]["text"] = default_text

    # Typography
    raw_typo = raw.get("typography", {})
    result["typography"] = {**_DEFAULT_TYPOGRAPHY}
    if isinstance(raw_typo, dict):
        if "headingFamily" in raw_typo:
            result["typography"]["headingFamily"] = raw_typo["headingFamily"]
        if "bodyFamily" in raw_typo:
            result["typography"]["bodyFamily"] = raw_typo["bodyFamily"]
        if "scale" in raw_typo and isinstance(raw_typo["scale"], list):
            result["typography"]["scale"] = raw_typo["scale"]

    # Spacing
    raw_spacing = raw.get("spacing", {})
    result["spacing"] = {**_DEFAULT_SPACING}
    if isinstance(raw_spacing, dict):
        if "scale" in raw_spacing and isinstance(raw_spacing["scale"], list):
            result["spacing"]["scale"] = raw_spacing["scale"]
        for key in ("sectionGap", "componentGap"):
            if key in raw_spacing:
                result["spacing"][key] = raw_spacing[key]

    # Borders
    raw_borders = raw.get("borders", {})
    result["borders"] = {**_DEFAULT_BORDERS}
    if isinstance(raw_borders, dict):
        if "radii" in raw_borders:
            result["borders"]["radii"] = raw_borders["radii"]
        if "widths" in raw_borders:
            result["borders"]["widths"] = raw_borders["widths"]

    # Shadows
    result["shadows"] = raw.get("shadows", [])

    # Sections
    result["sections"] = raw.get("sections", [])
    if not isinstance(result["sections"], list):
        result["sections"] = []

    # Components
    result["components"] = raw.get("components", [])
    if not isinstance(result["components"], list):
        result["components"] = []

    # Voice
    raw_voice = raw.get("voice", {})
    result["voice"] = {**_DEFAULT_VOICE}
    if isinstance(raw_voice, dict):
        for key in _DEFAULT_VOICE:
            if key in raw_voice:
                result["voice"][key] = raw_voice[key]

    return result


def merge_designs(designs: list[dict]) -> dict:
    """Merge multiple normalized design objects into one.

    Merge strategy:
    - Colors: first design's primary/secondary/accent win; union neutrals & gradients
    - Typography: first design's families win; union type scales (dedupe by role)
    - Spacing: union scales
    - Sections: concatenate in order
    - Components: union by kind, merge variant/size lists
    - Voice: first design wins

    Args:
        designs: List of normalized design dicts.

    Returns:
        Single merged design dict.
    """
    if not designs:
        return normalize_vision_response({})

    if len(designs) == 1:
        return copy.deepcopy(designs[0])

    merged = copy.deepcopy(designs[0])

    for d in designs[1:]:
        # ── Colors: union neutrals and gradients ──
        existing_neutrals = set(merged["colors"].get("neutrals", []))
        for n in d["colors"].get("neutrals", []):
            existing_neutrals.add(n)
        merged["colors"]["neutrals"] = sorted(existing_neutrals)

        existing_grads = set(merged["colors"].get("gradients", []))
        for g in d["colors"].get("gradients", []):
            existing_grads.add(g)
        merged["colors"]["gradients"] = sorted(existing_grads)

        # ── Typography: deduplicate scale by role ──
        existing_roles = {entry["role"]: entry for entry in merged["typography"].get("scale", [])}
        for entry in d["typography"].get("scale", []):
            if entry["role"] not in existing_roles:
                existing_roles[entry["role"]] = entry
        merged["typography"]["scale"] = list(existing_roles.values())

        # ── Spacing: union scales ──
        existing_spacing = set(merged["spacing"].get("scale", []))
        for s in d["spacing"].get("scale", []):
            existing_spacing.add(s)
        merged["spacing"]["scale"] = sorted(existing_spacing)

        # ── Sections: concatenate ──
        merged["sections"].extend(d.get("sections", []))

        # ── Components: union by kind, merge variants/sizes ──
        existing_kinds = {c["kind"]: c for c in merged["components"]}
        for comp in d.get("components", []):
            kind = comp["kind"]
            if kind in existing_kinds:
                # Merge variants
                existing_variants = set(existing_kinds[kind].get("variants", []))
                for v in comp.get("variants", []):
                    existing_variants.add(v)
                existing_kinds[kind]["variants"] = sorted(existing_variants)
                # Merge sizes
                existing_sizes = set(existing_kinds[kind].get("sizes", []))
                for s in comp.get("sizes", []):
                    existing_sizes.add(s)
                existing_kinds[kind]["sizes"] = sorted(existing_sizes)
                # Merge sample text
                existing_text = set(existing_kinds[kind].get("sampleText", []))
                for t in comp.get("sampleText", []):
                    existing_text.add(t)
                existing_kinds[kind]["sampleText"] = sorted(existing_text)
            else:
                existing_kinds[kind] = copy.deepcopy(comp)

        merged["components"] = list(existing_kinds.values())

    return merged


def write_outputs(design: dict, project_dir: str) -> list[str]:
    """Write the 4 canonical output files to the project directory.

    Files written:
    - design-tokens.json     (W3C DTCG format)
    - component-spec.json    (component anatomy + props)
    - section-map.json       (page section roles)
    - design-brief.md        (LLM-ready system prompt)

    Args:
        design: Normalized+merged design dict.
        project_dir: Absolute path to the project root.

    Returns:
        List of absolute paths to the written files.
    """
    # Formatters are imported/defined at module level (lines 407+)
    # format_dtcg_tokens, format_component_spec, format_section_map, format_design_brief

    written = []
    # RCA-461 path audit: All planning artifacts must go under docs/.
    # Writing to project root caused downstream readers using _planning_path()
    # (e.g., codebase_state_injector.py) to miss the files.
    docs_dir = os.path.join(project_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)

    # 1. design-tokens.json → docs/design-tokens.json (canonical)
    tokens = format_dtcg_tokens(design)
    tokens_path = os.path.join(docs_dir, "design-tokens.json")
    with open(tokens_path, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)
    written.append(tokens_path)

    # 2. component-spec.json → docs/component-spec.json (canonical)
    comp_spec = format_component_spec(design)
    comp_path = os.path.join(docs_dir, "component-spec.json")
    with open(comp_path, "w", encoding="utf-8") as f:
        json.dump(comp_spec, f, indent=2)
    written.append(comp_path)

    # 3. section-map.json → docs/section-map.json
    sec_map = format_section_map(design)
    sec_path = os.path.join(docs_dir, "section-map.json")
    with open(sec_path, "w", encoding="utf-8") as f:
        json.dump(sec_map, f, indent=2)
    written.append(sec_path)

    # 4. design-brief.md → docs/design-brief.md
    brief = format_design_brief(design)
    brief_path = os.path.join(docs_dir, "design-brief.md")
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(brief)
    written.append(brief_path)

    return written


# ═══════════════════════════════════════════════════════════════════════
# FORMATTER IMPORTS — built by another subagent. Graceful fallback if
# not yet available (tests mock these).
# ═══════════════════════════════════════════════════════════════════════

try:
    from python.helpers.dtcg_token_formatter import format_dtcg_tokens
except ImportError:
    def format_dtcg_tokens(design: dict) -> dict:
        """Fallback: minimal DTCG token format."""
        logger.warning("[extract_design_spec] dtcg_token_formatter not found — using fallback")
        return {
            "$metadata": {"generator": "agix-extract-design-spec", "spec": "W3C DTCG"},
            "primitive": {
                "color": {
                    "brand": {
                        "primary": {"$value": design.get("colors", {}).get("primary", "#000"), "$type": "color"}
                    }
                }
            },
            "semantic": {},
        }

try:
    from python.helpers.design_spec_formatters import format_component_spec
except ImportError:
    def format_component_spec(design: dict) -> dict:
        """Fallback: pass-through component spec."""
        logger.warning("[extract_design_spec] design_spec_formatters not found — using fallback")
        return {
            "$metadata": {"generator": "agix-extract-design-spec"},
            "components": design.get("components", []),
        }

try:
    from python.helpers.design_spec_formatters import format_section_map
except ImportError:
    def format_section_map(design: dict) -> dict:
        """Fallback: pass-through section map."""
        logger.warning("[extract_design_spec] design_spec_formatters not found — using fallback")
        sections = design.get("sections", [])
        for i, s in enumerate(sections):
            if "order" not in s:
                s["order"] = i
        return {
            "$metadata": {"generator": "agix-extract-design-spec"},
            "sections": sections,
        }

try:
    from python.helpers.design_spec_formatters import format_design_brief
except ImportError:
    def format_design_brief(design: dict) -> str:
        """Fallback: minimal design brief."""
        logger.warning("[extract_design_spec] design_spec_formatters not found — using fallback")
        colors = design.get("colors", {})
        typo = design.get("typography", {})
        lines = [
            "# Design Brief",
            "",
            "## Colour",
            f"- primary      {colors.get('primary', 'N/A')}",
            f"- background   {colors.get('background', 'N/A')}",
            "",
            "## Typography",
            f"- heading      {typo.get('headingFamily', 'sans-serif')}",
            f"- body         {typo.get('bodyFamily', 'sans-serif')}",
            "",
            "## Build Rules",
            "1. Use the colours above. Never invent a new hex.",
            "2. Snap spacing to the token scale.",
            "3. Use the component anatomy from component-spec.json.",
        ]
        return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════════
# TOOL CLASS
# ═══════════════════════════════════════════════════════════════════════


class ExtractDesignSpec(Tool):
    """Extract structured design specs from mockup images.

    PRIMARY INPUT: Image set (1–N mockups). Our system generates 5 pages;
    AGI frontend marketing sends 3 via work package → webhook.

    Input params (checked in order):
      - image_paths:   Explicit list of image files (from generate_image calls)
      - image_dir:     Directory to scan for all images
      - (none):        Auto-discovers images in docs/design-mockups/, docs/mockups/, etc.

    Outputs (all written to deterministic project paths):
    1. design-tokens.json     — W3C DTCG format (primitive + semantic tiers)
    2. component-spec.json    — Component tree with typed props + slots
    3. section-map.json       — Page section roles (hero, pricing, etc.)
    4. design-brief.md        — LLM-ready system prompt for code agent
    """

    async def execute(self, **kwargs) -> Response:
        await self.agent.handle_intervention()

        # ── Resolve inputs ──
        image_paths = self.args.get("image_paths", [])
        image_dir = self.args.get("image_dir", "")

        # Parse image_paths if provided as comma-separated string
        if isinstance(image_paths, str):
            image_paths = [p.strip() for p in image_paths.split(",") if p.strip()]

        # ── Resolve project directory ──
        try:
            from python.helpers.resolve_agent_path import resolve_agent_path
            project_dir = resolve_agent_path("", self.agent)
        except Exception as e:
            logger.error(f"[extract_design_spec] Failed to resolve project dir: {e}")
            return Response(
                message=f"Error: Could not resolve project directory: {e}",
                break_loop=False,
            )

        # ── Discover images ──
        if not image_paths:
            if image_dir:
                # Resolve image_dir relative to project
                if not os.path.isabs(image_dir):
                    image_dir = os.path.join(project_dir, image_dir)
                image_paths = discover_images(image_dir)
            else:
                # Auto-discover from canonical project locations
                for candidate in _AUTO_DISCOVER_DIRS:
                    candidate_path = os.path.join(project_dir, candidate)
                    found = discover_images(candidate_path)
                    if found:
                        image_paths = found
                        PrintStyle(font_color="#8E44AD", bold=True, padding=True).print(
                            f"Auto-discovered {len(found)} mockup(s) in {candidate}/"
                        )
                        break

        if not image_paths:
            return Response(
                message="No design input found. Place mockups in docs/design-mockups/ "
                        "or provide image_paths/image_dir.",
                break_loop=False,
            )

        # ── Extract from each image ──
        PrintStyle(font_color="#8E44AD", bold=True, padding=True).print(
            f"Extracting design spec from {len(image_paths)} image(s)..."
        )

        page_designs = []
        for i, img_path in enumerate(image_paths):
            # Resolve relative paths against project dir
            if not os.path.isabs(img_path):
                img_path = os.path.join(project_dir, img_path)

            if not os.path.isfile(img_path):
                logger.warning(f"[extract_design_spec] Image not found: {img_path}")
                continue

            PrintStyle(font_color="#8E44AD", padding=True).print(
                f"  [{i + 1}/{len(image_paths)}] Analyzing: {os.path.basename(img_path)}"
            )

            try:
                raw = await self._call_vision_llm(img_path)
                normalized = normalize_vision_response(raw)
                page_designs.append(normalized)
            except Exception as e:
                logger.error(f"[extract_design_spec] Vision LLM failed for {img_path}: {e}")
                PrintStyle(font_color="#E74C3C", padding=True).print(
                    f"  ⚠ Failed to analyze {os.path.basename(img_path)}: {e}"
                )

        if not page_designs:
            return Response(
                message="Error: Vision LLM extraction failed for all images. "
                        "Check API key and model configuration.",
                break_loop=True,
            )

        # ── Merge all extractions ──
        design = merge_designs(page_designs)

        # ── Write outputs ──
        written = write_outputs(design, project_dir)

        # ── Build response ──
        file_list = "\n".join(f"  - {os.path.relpath(p, project_dir)}" for p in written)
        summary = (
            f"✅ Design spec extracted from {len(page_designs)} mockup(s).\n\n"
            f"Files written:\n{file_list}\n\n"
            f"Components: {len(design['components'])} kinds\n"
            f"Sections: {len(design['sections'])} page sections\n"
            f"Colors: {design['colors'].get('primary', 'N/A')} (primary)"
        )

        PrintStyle(font_color="#8E44AD", bold=True, padding=True).print(summary)

        return Response(
            message=summary,
            break_loop=False,
            additional={
                "type": "design_spec",
                "files": [os.path.relpath(p, project_dir) for p in written],
                "components_count": len(design["components"]),
                "sections_count": len(design["sections"]),
            },
        )

    async def _call_vision_llm(self, image_path: str) -> dict:
        """Call Vision LLM to extract design data from a single image.

        Uses the same provider/model infrastructure as generate_image.
        Sends the image as base64 with the EXTRACTION_PROMPT.

        Args:
            image_path: Absolute path to the image file.

        Returns:
            Parsed JSON dict from the Vision LLM response.

        Raises:
            RuntimeError: If the API call fails or returns unparseable response.
        """
        from python.helpers import settings
        from python.helpers.model_wrappers.utils import get_api_key

        # Read and encode the image
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        b64_data = base64.b64encode(img_bytes).decode("utf-8")

        # Detect MIME type
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/png")

        # Get Vision config — use chat model for Vision (not image gen model)
        provider, model = self._get_vision_config()
        api_key = get_api_key(provider)

        if not api_key or api_key in ("None", "NA", ""):
            raise RuntimeError(
                f"No API key found for provider '{provider}'. "
                f"Set it in Settings > Secrets or as an environment variable."
            )

        # Call via OpenAI-compatible API (works for OpenRouter + OpenAI)
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai package is not installed.")

        if provider == "openrouter":
            base_url = "https://openrouter.ai/api/v1"
            full_model = model
        elif provider == "gemini":
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
            full_model = model
        else:
            base_url = None
            full_model = model

        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        client = OpenAI(**client_kwargs)

        response = client.chat.completions.create(
            model=full_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": EXTRACTION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
                        },
                    ],
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=4096,
        )

        # Parse the response
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Vision LLM returned empty response.")

        # Strip markdown fences if present
        content = content.strip()
        if content.startswith("```"):
            # Remove ```json and ``` fences
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Vision LLM returned invalid JSON: {e}\nRaw: {content[:500]}")

    def _get_vision_config(self) -> tuple[str, str]:
        """Get Vision LLM model config.

        Uses the chat model (not image gen model) since we need
        vision understanding, not image generation.
        """
        from python.helpers import settings
        try:
            s = settings.get_settings()
            # Use the main chat model for vision — it supports image input
            provider = s.get("chat_model_provider", "openrouter")
            model = s.get("chat_model_name", "google/gemini-2.5-flash")
        except Exception:
            provider = "openrouter"
            model = "google/gemini-2.5-flash"

        # Env var overrides
        provider = os.environ.get("VISION_PROVIDER", provider).lower()
        model = os.environ.get("VISION_MODEL", model)

        return provider, model
