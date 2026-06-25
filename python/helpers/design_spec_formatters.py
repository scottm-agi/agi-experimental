"""
Design Spec Formatters — component-spec.json, section-map.json, design-brief.md

Ported from design-extract's agent-prompt.js (215 lines) and
component-anatomy.js (124 lines) for AGIX's native extract_design_spec
tool (ADR-83).

Three formatter functions:
  format_component_spec(design) → dict  — component-spec.json content
  format_section_map(design)    → dict  — section-map.json content
  format_design_brief(design, project_name) → str — design-brief.md content

Input: A normalized Vision LLM extraction dict.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────

_GENERATOR = "agix-extract-design-spec"

# Default slot definitions per component kind
_DEFAULT_SLOTS: Dict[str, dict] = {
    "button": {
        "children": {"required": True},
        "leadingIcon": {"required": False},
        "trailingIcon": {"required": False},
    },
    "card": {
        "children": {"required": True},
        "heading": {"required": False},
        "description": {"required": False},
        "media": {"required": False},
        "footer": {"required": False},
    },
    "input": {
        "children": {"required": False},
        "leadingIcon": {"required": False},
        "trailingIcon": {"required": False},
    },
    "badge": {
        "children": {"required": True},
    },
    "avatar": {
        "children": {"required": False},
    },
}


# ────────────────────────────────────────────────────────────────────────────
# Component Spec Formatter
# ────────────────────────────────────────────────────────────────────────────

def _build_component_slots(kind: str, raw_slots: Optional[dict]) -> dict:
    """Build typed slot definitions from raw slot data.

    Merges raw slot data from Vision LLM with default slots for the kind.
    """
    # Start with default slots for this kind
    default = _DEFAULT_SLOTS.get(kind, {"children": {"required": True}})
    slots: Dict[str, dict] = {}

    for slot_name, slot_def in default.items():
        slots[slot_name] = dict(slot_def)

    # Overlay raw slots if present
    if isinstance(raw_slots, dict):
        for slot_name, slot_val in raw_slots.items():
            if isinstance(slot_val, bool):
                # Vision LLM returns {label: true, icon: true, badge: false}
                if slot_val:
                    canonical_name = _canonicalize_slot_name(slot_name, kind)
                    if canonical_name not in slots:
                        slots[canonical_name] = {"required": False}
            elif isinstance(slot_val, dict):
                slots[slot_name] = slot_val

    return slots


def _canonicalize_slot_name(name: str, kind: str) -> str:
    """Map Vision LLM slot names to canonical React slot names."""
    mapping = {
        "label": "children",
        "icon": "leadingIcon",
        "leading": "leadingIcon",
        "trailing": "trailingIcon",
    }
    return mapping.get(name, name)


def _build_component_props(component: dict) -> dict:
    """Build typed prop definitions from a Vision LLM component dict.

    Input shape:
      {kind, variants: ['primary', 'secondary'], sizes: ['sm', 'md', 'lg'], ...}

    Output shape per ADR-83 §4.5:
      {variant: {type: 'enum', values: [...], default: ...}, size: {type: 'enum', ...}}
    """
    props: Dict[str, dict] = {}

    # Variant prop
    variants = component.get("variants", [])
    if isinstance(variants, list) and variants:
        props["variant"] = {
            "type": "enum",
            "values": list(variants),
            "default": variants[0],
        }

    # Size prop
    sizes = component.get("sizes", [])
    if isinstance(sizes, list) and sizes:
        # Pick "md" as default if available, else first
        default_size = "md" if "md" in sizes else sizes[0]
        props["size"] = {
            "type": "enum",
            "values": list(sizes),
            "default": default_size,
        }

    # Disabled prop (always boolean, default false)
    props["disabled"] = {"type": "boolean", "default": False}

    return props


def format_component_spec(design: dict) -> dict:
    """Format a design dict into component-spec.json content.

    Args:
        design: Normalized Vision LLM extraction result.

    Returns:
        Dict matching ADR-83 §4.5 component-spec.json schema.
    """
    metadata = {"generator": _GENERATOR}

    raw_components: list = design.get("components", [])
    components: List[dict] = []

    for comp in raw_components:
        if not isinstance(comp, dict):
            continue

        kind = comp.get("kind", "unknown")
        display_name = kind.capitalize()

        # Build props
        props = _build_component_props(comp)

        # Build slots
        slots = _build_component_slots(kind, comp.get("slots"))

        # Sample content — preserve actual text from mockup
        sample_content = comp.get("sampleText", [])
        if isinstance(sample_content, str):
            sample_content = [sample_content]

        component_spec: Dict[str, Any] = {
            "kind": kind,
            "displayName": display_name,
            "props": props,
            "slots": slots,
            "sampleContent": list(sample_content),
        }

        components.append(component_spec)

    return {
        "$metadata": metadata,
        "components": components,
    }


# ────────────────────────────────────────────────────────────────────────────
# Section Map Formatter
# ────────────────────────────────────────────────────────────────────────────

def format_section_map(design: dict) -> dict:
    """Format a design dict into section-map.json content.

    Args:
        design: Normalized Vision LLM extraction result.

    Returns:
        Dict matching ADR-83 §4.5 section-map.json schema.
    """
    metadata = {"generator": _GENERATOR}

    raw_sections: list = design.get("sections", [])
    sections: List[dict] = []

    for i, sec in enumerate(raw_sections):
        if not isinstance(sec, dict):
            continue

        role = sec.get("role", "unknown")
        heading = sec.get("heading")
        subheading = sec.get("subheading")
        components = sec.get("components", [])

        section_entry: Dict[str, Any] = {
            "role": role,
            "order": i,
            "heading": heading,
            "subheading": subheading,
            "components": list(components) if isinstance(components, list) else [],
        }

        # CTA — only include if there's actual CTA text
        cta_text = sec.get("ctaText")
        cta_action = sec.get("ctaAction")
        if cta_text:
            section_entry["cta"] = {
                "text": cta_text,
                "href": cta_action,
            }
        else:
            section_entry["cta"] = None

        sections.append(section_entry)

    # Sort by order (already sequential from enumeration, but ensure)
    sections.sort(key=lambda s: s["order"])

    return {
        "$metadata": metadata,
        "sections": sections,
    }


# ────────────────────────────────────────────────────────────────────────────
# Design Brief Formatter (Markdown)
# ────────────────────────────────────────────────────────────────────────────

def _hex(c: Any) -> Optional[str]:
    """Extract hex value from a color entry."""
    if c is None:
        return None
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        return c.get("hex")
    return None


def _list_colors(design: dict) -> str:
    """Build colour section lines."""
    colors = design.get("colors", {})
    lines: List[str] = []

    for role in ["primary", "secondary", "accent", "background", "surface"]:
        v = _hex(colors.get(role))
        if v:
            lines.append(f"- {role:<12} {v}")

    # Neutrals
    raw_neutrals = colors.get("neutrals", [])
    neutral_hexes = [_hex(n) for n in raw_neutrals if _hex(n)]
    if neutral_hexes:
        lines.append(f"- {'neutrals':<12} {' · '.join(neutral_hexes[:6])}")

    return "\n".join(lines) if lines else "_(no colour roles detected)_"


def _list_typography(design: dict) -> str:
    """Build typography section lines."""
    typography = design.get("typography", {})
    lines: List[str] = []

    families: List[str] = []
    for key in ["headingFamily", "bodyFamily"]:
        fam = typography.get(key)
        if fam and fam not in families:
            families.append(fam)
    if families:
        lines.append(f"- {'families':<12} {' · '.join(families)}")

    # Scale entries
    scale = typography.get("scale", [])
    for entry in scale:
        if isinstance(entry, dict):
            role = entry.get("role", "?")
            size = entry.get("size", "?")
            weight = entry.get("weight", "?")
            lines.append(f"- {role:<12} {size}, weight {weight}")

    return "\n".join(lines) if lines else "_(no typography detected)_"


def _list_spacing(design: dict) -> Optional[str]:
    """Build spacing scale line."""
    scale = design.get("spacing", {}).get("scale", [])
    if not scale:
        return None
    values = " ".join(str(v) for v in scale)
    return f"- scale        {values}"


def _list_voice(design: dict) -> Optional[str]:
    """Build voice section lines."""
    voice = design.get("voice", {})
    if not isinstance(voice, dict) or not voice:
        return None

    lines: List[str] = []
    if voice.get("tone"):
        lines.append(f"- {'tone':<12} {voice['tone']}")
    if voice.get("pronoun"):
        lines.append(f"- {'pronoun':<12} {voice['pronoun']}")
    if voice.get("headingStyle"):
        lines.append(f"- {'headings':<12} {voice['headingStyle']}")

    cta_verbs = voice.get("ctaVerbs", [])
    if isinstance(cta_verbs, list) and cta_verbs:
        lines.append(f"- {'CTA verbs':<12} {' · '.join(str(v) for v in cta_verbs)}")

    return "\n".join(lines) if lines else None


def format_design_brief(design: dict, project_name: str = "") -> str:
    """Format a design dict into design-brief.md content.

    Adapted from design-extract's agent-prompt.js — produces a self-contained
    system prompt with build rules for the code agent.

    Args:
        design: Normalized Vision LLM extraction result.
        project_name: Optional project name for the header.

    Returns:
        Markdown string matching ADR-83 §4.5 design-brief.md format.
    """
    title = project_name or "this project"

    blocks: List[Optional[str]] = []

    blocks.append(f"# You are building UI for {title}.")
    blocks.append("")
    blocks.append(f"Extracted by agix-extract-design-spec.")
    blocks.append("")

    # Colour section
    blocks.append("## Colour")
    blocks.append("")
    blocks.append(_list_colors(design))
    blocks.append("")

    # Typography section
    blocks.append("## Typography")
    blocks.append("")
    blocks.append(_list_typography(design))
    blocks.append("")

    # Spacing section
    spacing = _list_spacing(design)
    if spacing:
        blocks.append("## Spacing")
        blocks.append("")
        blocks.append(spacing)
        blocks.append("")

    # Voice section
    voice = _list_voice(design)
    if voice:
        blocks.append("## Voice")
        blocks.append("")
        blocks.append(voice)
        blocks.append("")

    # Build rules (always present — ported from agent-prompt.js)
    blocks.append("## Build Rules")
    blocks.append("")
    blocks.append(
        "1. Use the colours above. **Never invent a new hex.** If you need a"
    )
    blocks.append(
        "   shade between two existing colours, derive it via HSL adjustment"
    )
    blocks.append("   from the closest extracted colour and call out the derivation.")
    blocks.append(
        "2. Use the extracted typography families. If you need a missing weight,"
    )
    blocks.append("   pick the nearest available weight from the list and note it.")
    blocks.append(
        "3. Snap spacing values to the token scale. No off-scale paddings or"
    )
    blocks.append("   margins.")
    blocks.append("4. Snap border radii to the scale above.")
    blocks.append(
        "5. Match the voice: same tone, same pronoun stance, same heading"
    )
    blocks.append("   style. Reuse the listed CTA verbs.")
    blocks.append(
        "6. Aim for WCAG AA contrast minimum. When the brand colours fail,"
    )
    blocks.append(
        "   prefer the foreground colour on the background colour rather than"
    )
    blocks.append("   mid-tone neutrals.")
    blocks.append(
        "7. Reuse component anatomy when it exists — do not invent novel"
    )
    blocks.append("   structures for things the site already has.")
    blocks.append("")

    # Available context files
    blocks.append("## Available context files")
    blocks.append("")
    blocks.append(
        "The extract_design_spec tool wrote these alongside this brief. "
        "Reach for them when you need ground truth:"
    )
    blocks.append("")
    blocks.append("- `design-tokens.json` — DTCG primitive · semantic tokens")
    blocks.append("- `component-spec.json` — component anatomy with typed props")
    blocks.append("- `section-map.json` — page section roles and structure")
    blocks.append("")
    blocks.append(
        "When you reference the system in code, prefer importing from these"
    )
    blocks.append("files over hard-coding values.")
    blocks.append("")

    return "\n".join(b for b in blocks if b is not None)
