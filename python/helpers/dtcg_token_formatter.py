"""
W3C DTCG Token Formatter — produces design-tokens.json

Ported from design-extract's dtcg-tokens.js (176 lines) for AGIX's
native extract_design_spec tool (ADR-83).

Input: A normalized Vision LLM extraction dict with keys:
  colors, typography, spacing, borders, shadows, etc.

Output: W3C DTCG-compliant dict with 3 tiers:
  $metadata — generator info + spec URL
  primitive — raw tokens (color, spacing, radius, shadow, fontFamily)
  semantic  — reference tokens using {primitive.X.Y} syntax

Spec: https://design-tokens.github.io/community-group/format/
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union


# ────────────────────────────────────────────────────────────────────────────
# Token helpers
# ────────────────────────────────────────────────────────────────────────────

def _token(value: Any, type_name: str, extensions: Optional[dict] = None) -> dict:
    """Create a DTCG token leaf: {$value, $type[, $extensions]}."""
    t: Dict[str, Any] = {"$value": value, "$type": type_name}
    if extensions:
        t["$extensions"] = extensions
    return t


def _ref(path: str) -> str:
    """Create a DTCG reference string: {path}."""
    return "{" + path + "}"


# ────────────────────────────────────────────────────────────────────────────
# Value normalizers (handle multiple input shapes from Vision LLM)
# ────────────────────────────────────────────────────────────────────────────

def _color_value(v: Any) -> Optional[str]:
    """Normalize a color entry — may be a string hex or {hex} object."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, dict) and isinstance(v.get("hex"), str):
        return v["hex"]
    return None


def _dimension_value(v: Any) -> Optional[str]:
    """Normalize a dimension — may be str ('4px'), int (4), or {value} object."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return f"{int(v)}px"
    if isinstance(v, dict):
        inner = v.get("value")
        if isinstance(inner, (int, float)):
            return f"{int(inner)}px"
        if isinstance(inner, str):
            return inner
    return None


def _shadow_value(v: Any) -> Optional[str]:
    """Normalize a shadow — may be str, {value}, or {raw} object."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        if isinstance(v.get("value"), str):
            return v["value"]
        if isinstance(v.get("raw"), str):
            return v["raw"]
    return None


def _font_family_value(v: Any) -> Optional[str]:
    """Normalize a font family — may be str or {name} object."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, dict) and isinstance(v.get("name"), str):
        return v["name"]
    return None


def _font_size_value(v: Any) -> Optional[str]:
    """Normalize a font size — may be int (px) or str ('16px')."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return f"{int(v)}px"
    return None


# ────────────────────────────────────────────────────────────────────────────
# Primitive tier builder
# ────────────────────────────────────────────────────────────────────────────

def _build_primitive(design: dict) -> dict:
    """Build the primitive token tier from a design dict.

    The Vision LLM output shape differs from design-extract's crawler output.
    Our design dict has:
      colors.primary, colors.secondary, colors.accent — brand colors (strings)
      colors.background, colors.surface — background colors (strings)
      colors.text — dict with primary/secondary/muted keys (or list)
      colors.neutrals — list of hex strings
    """
    colors = design.get("colors", {})

    # ── Brand colors ──
    brand_primary = _color_value(colors.get("primary")) or "#000000"
    brand_secondary = _color_value(colors.get("secondary"))
    brand_accent = _color_value(colors.get("accent"))

    brand: Dict[str, dict] = {"primary": _token(brand_primary, "color")}
    if brand_secondary:
        brand["secondary"] = _token(brand_secondary, "color")
    if brand_accent:
        brand["accent"] = _token(brand_accent, "color")

    # ── Neutral colors (n100, n200, …) ──
    neutral: Dict[str, dict] = {}
    raw_neutrals: list = colors.get("neutrals", [])
    for i, n in enumerate(raw_neutrals):
        cv = _color_value(n)
        if cv:
            neutral[f"n{(i + 1) * 100}"] = _token(cv, "color")

    # ── Background colors (bg0, bg1, …) ──
    background: Dict[str, dict] = {}
    # Collect from explicit backgrounds list, or from background + surface fields
    raw_backgrounds: list = colors.get("backgrounds", [])
    if not raw_backgrounds:
        # Build from individual fields
        bg_val = _color_value(colors.get("background"))
        surface_val = _color_value(colors.get("surface"))
        if bg_val:
            raw_backgrounds.append(bg_val)
        if surface_val:
            raw_backgrounds.append(surface_val)
    for i, bg in enumerate(raw_backgrounds):
        cv = _color_value(bg)
        if cv:
            background[f"bg{i}"] = _token(cv, "color")

    # ── Text colors (text0, text1, …) ──
    text: Dict[str, dict] = {}
    raw_text = colors.get("text", {})
    if isinstance(raw_text, dict):
        # Dict with named keys like {primary, secondary, muted}
        text_values: List[Optional[str]] = []
        for key in ["primary", "secondary", "muted"]:
            val = _color_value(raw_text.get(key))
            if val:
                text_values.append(val)
        # Also include any additional keys not in the standard set
        for key, val in raw_text.items():
            if key not in ("primary", "secondary", "muted"):
                cv = _color_value(val)
                if cv:
                    text_values.append(cv)
        for i, tv in enumerate(text_values):
            text[f"text{i}"] = _token(tv, "color")
    elif isinstance(raw_text, list):
        for i, tv in enumerate(raw_text):
            cv = _color_value(tv)
            if cv:
                text[f"text{i}"] = _token(cv, "color")

    color = {
        "brand": brand,
        "neutral": neutral,
        "background": background,
        "text": text,
    }

    # ── Spacing (s0, s1, …) ──
    spacing: Dict[str, dict] = {}
    raw_spacing: list = design.get("spacing", {}).get("scale", [])
    for i, s in enumerate(raw_spacing):
        dv = _dimension_value(s)
        if dv:
            spacing[f"s{i}"] = _token(dv, "dimension")

    # ── Radius (r0, r1, …) ──
    radius: Dict[str, dict] = {}
    raw_radii: list = design.get("borders", {}).get("radii", [])
    for i, r in enumerate(raw_radii):
        dv = _dimension_value(r)
        if dv:
            radius[f"r{i}"] = _token(dv, "dimension")

    # ── Shadow (sh0, sh1, …) ──
    shadow: Dict[str, dict] = {}
    raw_shadows = design.get("shadows", [])
    # Handle both list-of-objects and list-of-strings
    for i, s in enumerate(raw_shadows):
        sv = _shadow_value(s)
        if sv:
            shadow[f"sh{i}"] = _token(sv, "shadow")

    # ── Font families (f0, f1, …) ──
    font_family: Dict[str, dict] = {}
    typography = design.get("typography", {})
    # Collect unique font families from headingFamily + bodyFamily
    raw_families: list = typography.get("families", [])
    if not raw_families:
        seen: set = set()
        for key in ["headingFamily", "bodyFamily"]:
            fv = _font_family_value(typography.get(key))
            if fv and fv not in seen:
                raw_families.append(fv)
                seen.add(fv)
    for i, f in enumerate(raw_families):
        fv = _font_family_value(f)
        if fv:
            font_family[f"f{i}"] = _token(fv, "fontFamily")

    return {
        "color": color,
        "spacing": spacing,
        "radius": radius,
        "shadow": shadow,
        "fontFamily": font_family,
    }


# ────────────────────────────────────────────────────────────────────────────
# Semantic tier builder
# ────────────────────────────────────────────────────────────────────────────

def _build_semantic(design: dict, primitive: dict) -> dict:
    """Build semantic reference tokens from primitive tier.

    Semantic tokens reference primitives using {primitive.X.Y} syntax,
    providing role-based aliases for the raw tokens.
    """
    first_radius_key = next(iter(primitive["radius"]), "r0")
    first_shadow_key = next(iter(primitive["shadow"]), "sh0")

    # ── Semantic colors ──
    color: Dict[str, dict] = {
        "action": {
            "primary": _token(_ref("primitive.color.brand.primary"), "color"),
        },
        "surface": {
            "default": _token(_ref("primitive.color.background.bg0"), "color"),
        },
        "text": {
            "body": _token(_ref("primitive.color.text.text0"), "color"),
        },
    }
    if "secondary" in primitive["color"]["brand"]:
        color["action"]["secondary"] = _token(
            _ref("primitive.color.brand.secondary"), "color"
        )

    # ── Semantic typography (composite token) ──
    typography_data = design.get("typography", {})

    # Find the body scale entry for composite typography
    body_family = _font_family_value(typography_data.get("bodyFamily")) or "system-ui"
    body_scale = {}
    for entry in typography_data.get("scale", []):
        if isinstance(entry, dict) and entry.get("role") == "body":
            body_scale = entry
            break
    # If no explicit body entry, use the first scale entry
    if not body_scale and typography_data.get("scale"):
        body_scale = typography_data["scale"][0]

    typography = {
        "body": _token(
            {
                "fontFamily": body_family,
                "fontSize": _font_size_value(body_scale.get("size")) or "16px",
                "fontWeight": str(body_scale.get("weight", "400")),
                "lineHeight": str(body_scale.get("lineHeight", "1.5")),
            },
            "typography",
        ),
    }

    # ── Semantic radius ──
    radius = {
        "control": _token(
            _ref(f"primitive.radius.{first_radius_key}"), "dimension"
        ),
    }

    # ── Semantic shadow ──
    shadow = {
        "elevated": _token(
            _ref(f"primitive.shadow.{first_shadow_key}"), "shadow"
        ),
    }

    return {
        "color": color,
        "typography": typography,
        "radius": radius,
        "shadow": shadow,
    }


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────

def format_dtcg_tokens(design: dict) -> dict:
    """Format a design dict into W3C DTCG-compliant design tokens.

    Args:
        design: Normalized Vision LLM extraction result with keys:
            colors, typography, spacing, borders, shadows, etc.

    Returns:
        Dict with $metadata, primitive, and semantic tiers.
    """
    primitive = _build_primitive(design)
    semantic = _build_semantic(design, primitive)

    metadata: Dict[str, str] = {
        "generator": "agix-extract-design-spec",
        "version": "1.0.0",
        "spec": "https://design-tokens.github.io/community-group/format/",
    }
    meta = design.get("meta", {})
    if isinstance(meta, dict):
        if meta.get("url"):
            metadata["source"] = meta["url"]
        if meta.get("timestamp"):
            metadata["generatedAt"] = meta["timestamp"]

    return {
        "$metadata": metadata,
        "primitive": primitive,
        "semantic": semantic,
    }
