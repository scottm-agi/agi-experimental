"""TDD-from-design-spec generator (ADR-83 G-7).

Reads component-spec.json and section-map.json to generate React Testing Library
test stub files. These tests are written BEFORE implementation (TDD), so they:
- Assert component rendering with correct props
- Test all variants and slots
- Verify page section ordering
- Include sample content assertions

Usage:
    from python.helpers.tdd_from_design_spec import generate_component_tests, generate_page_tests

    # Generate component tests
    with open("component-spec.json") as f:
        spec = json.load(f)
    tests = generate_component_tests(spec)  # {filename: content}

    # Generate page tests
    with open("section-map.json") as f:
        smap = json.load(f)
    page_tests = generate_page_tests(smap)  # {filename: content}
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger("tdd-from-design-spec")


def _pascal_case(kind: str) -> str:
    """Convert kebab-case or snake_case component kind to PascalCase.

    Examples:
        button → Button
        feature-card → FeatureCard
        nav_bar → NavBar
    """
    return "".join(word.capitalize() for word in kind.replace("-", "_").split("_"))


def _generate_single_component_test(component: dict, framework: str = "react") -> str:
    """Generate a test file for a single component.

    Args:
        component: Component dict from component-spec.json.
        framework: Target framework (currently only 'react' supported).

    Returns:
        Complete test file content as a string.
    """
    kind = component.get("kind", "unknown")
    display_name = component.get("displayName", _pascal_case(kind))
    pascal = _pascal_case(kind)
    variants = component.get("variants", [])
    if not variants:
        # Check props for variant enum
        props = component.get("props", {})
        variant_prop = props.get("variant", {})
        variants = variant_prop.get("values", [])

    sizes = component.get("sizes", [])
    if not sizes:
        props = component.get("props", {})
        size_prop = props.get("size", {})
        sizes = size_prop.get("values", [])

    slots = component.get("slots", {})
    sample_text = component.get("sampleContent", component.get("sampleText", []))

    lines = []

    # ── Imports ──
    lines.append(f'import {{ render, screen }} from "@testing-library/react";')
    lines.append(f'import {{ {pascal} }} from "@/components/ui/{pascal}";')
    lines.append("")
    lines.append(f'describe("{display_name}", () => {{')

    # ── Rendering test ──
    lines.append(f'  it("renders without crashing", () => {{')
    if "children" in slots:
        lines.append(f'    render(<{pascal}>Test</{pascal}>);')
    else:
        lines.append(f'    render(<{pascal} />);')
    lines.append(f"  }});")
    lines.append("")

    # ── Variant tests ──
    if variants:
        lines.append(f'  describe("variants", () => {{')
        for variant in variants:
            lines.append(f'    it("renders {variant} variant", () => {{')
            if "children" in slots:
                lines.append(f'      render(<{pascal} variant="{variant}">Test</{pascal}>);')
            else:
                lines.append(f'      render(<{pascal} variant="{variant}" />);')
            lines.append(f'      expect(screen.getByRole("button") || document.querySelector("[data-variant=\\"{variant}\\"]")).toBeTruthy();')
            lines.append(f"    }});")
            lines.append("")
        lines.append(f"  }});")
        lines.append("")

    # ── Slot tests ──
    if slots:
        lines.append(f'  describe("slots", () => {{')
        for slot_name, slot_config in slots.items():
            required = slot_config.get("required", False) if isinstance(slot_config, dict) else False
            lines.append(f'    it("renders {slot_name} slot", () => {{')
            if slot_name == "children":
                lines.append(f'      render(<{pascal}>Child Content</{pascal}>);')
                lines.append(f'      expect(screen.getByText("Child Content")).toBeInTheDocument();')
            elif slot_name in ("leadingIcon", "trailingIcon", "icon"):
                lines.append(f'      const TestIcon = () => <span data-testid="{slot_name}">icon</span>;')
                lines.append(f'      render(<{pascal} {slot_name}={{<TestIcon />}}>Test</{pascal}>);')
                lines.append(f'      expect(screen.getByTestId("{slot_name}")).toBeInTheDocument();')
            else:
                lines.append(f'      render(<{pascal} {slot_name}="Test {slot_name.capitalize()}" />);')
                lines.append(f'      expect(screen.getByText("Test {slot_name.capitalize()}")).toBeInTheDocument();')
            lines.append(f"    }});")
            lines.append("")
        lines.append(f"  }});")
        lines.append("")

    # ── Sample content tests ──
    if sample_text:
        lines.append(f'  describe("sample content", () => {{')
        for text in sample_text:
            safe_desc = text.replace('"', '\\"')[:50]
            lines.append(f'    it("renders sample text: {safe_desc}", () => {{')
            if "children" in slots:
                lines.append(f'      render(<{pascal}>{text}</{pascal}>);')
            else:
                # Use first slot that might hold text
                text_slot = next(
                    (s for s in slots if s in ("heading", "label", "title")),
                    None,
                )
                if text_slot:
                    lines.append(f'      render(<{pascal} {text_slot}="{text}" />);')
                else:
                    lines.append(f'      render(<{pascal}>{text}</{pascal}>);')
            lines.append(f'      expect(screen.getByText("{text}")).toBeInTheDocument();')
            lines.append(f"    }});")
            lines.append("")
        lines.append(f"  }});")
        lines.append("")

    lines.append(f"}});")
    lines.append("")

    return "\n".join(lines)


def generate_component_tests(
    component_spec: dict, framework: str = "react"
) -> dict[str, str]:
    """Generate test file content for each component.

    Reads the component-spec.json structure and produces one test file
    per component kind. Each test file includes:
    - Import statements for React Testing Library
    - Rendering test (smoke test)
    - Variant tests (one per variant)
    - Slot tests (one per slot)
    - Sample content tests (one per sample text entry)

    Args:
        component_spec: The full component-spec.json dict.
        framework: Target framework ('react' supported).

    Returns:
        Dict mapping filename → file content.
        Example: {"Button.test.tsx": "import { render..."}
    """
    components = component_spec.get("components", [])
    if not components:
        return {}

    result = {}
    for comp in components:
        kind = comp.get("kind", "unknown")
        pascal = _pascal_case(kind)
        filename = f"{pascal}.test.tsx"
        content = _generate_single_component_test(comp, framework)
        result[filename] = content

    return result


def _generate_page_test(section_map: dict, framework: str = "react") -> str:
    """Generate a page-level integration test from section map.

    The test verifies:
    - All sections render
    - Sections appear in correct order
    - Section headings match the spec

    Args:
        section_map: The full section-map.json dict.
        framework: Target framework.

    Returns:
        Complete test file content as a string.
    """
    sections = section_map.get("sections", [])
    if not sections:
        return ""

    # Sort by order field if present
    sorted_sections = sorted(sections, key=lambda s: s.get("order", 0))

    lines = []

    # ── Imports ──
    lines.append('import { render, screen } from "@testing-library/react";')
    lines.append('import Page from "@/app/page";')
    lines.append("")
    lines.append('describe("Page Integration", () => {')

    # ── Section presence tests ──
    lines.append('  describe("section rendering", () => {')
    for section in sorted_sections:
        role = section.get("role", "unknown")
        heading = section.get("heading", "")
        lines.append(f'    it("renders {role} section", () => {{')
        lines.append(f'      render(<Page />);')
        if heading:
            safe_heading = heading.replace('"', '\\"')
            lines.append(f'      expect(screen.getByText("{safe_heading}")).toBeInTheDocument();')
        lines.append(f'      // Section role: {role}')
        lines.append(f"    }});")
        lines.append("")
    lines.append("  });")
    lines.append("")

    # ── Section order test ──
    lines.append('  describe("section order", () => {')
    lines.append('    it("renders sections in correct order", () => {')
    lines.append("      render(<Page />);")
    lines.append("      const allSections = document.querySelectorAll('section, [data-section]');")
    lines.append(f"      expect(allSections.length).toBeGreaterThanOrEqual({len(sorted_sections)});")
    lines.append("")
    lines.append("      // Expected order of section roles:")
    for i, section in enumerate(sorted_sections):
        role = section.get("role", "unknown")
        lines.append(f'      // [{i}] {role}')
    lines.append("")
    lines.append("      // Verify order by checking headings appear in sequence")
    headings_with_text = [s for s in sorted_sections if s.get("heading")]
    if len(headings_with_text) >= 2:
        lines.append("      const bodyText = document.body.textContent || '';")
        for i in range(len(headings_with_text) - 1):
            h1 = headings_with_text[i]["heading"].replace('"', '\\"')
            h2 = headings_with_text[i + 1]["heading"].replace('"', '\\"')
            r1 = headings_with_text[i]["role"]
            r2 = headings_with_text[i + 1]["role"]
            lines.append(f'      const pos_{r1.replace("-", "_")} = bodyText.indexOf("{h1}");')
            lines.append(f'      const pos_{r2.replace("-", "_")} = bodyText.indexOf("{h2}");')
            lines.append(f'      expect(pos_{r1.replace("-", "_")}).toBeLessThan(pos_{r2.replace("-", "_")});')
    lines.append("    });")
    lines.append("  });")

    lines.append("});")
    lines.append("")

    return "\n".join(lines)


def generate_page_tests(
    section_map: dict, framework: str = "react"
) -> dict[str, str]:
    """Generate page-level integration test from section map.

    Reads the section-map.json structure and produces a page test that:
    - Verifies all sections render
    - Checks section ordering matches the spec
    - Validates section headings match

    Args:
        section_map: The full section-map.json dict.
        framework: Target framework ('react' supported).

    Returns:
        Dict mapping filename → file content.
        Example: {"Page.test.tsx": "import { render..."}
        Empty dict if no sections defined.
    """
    sections = section_map.get("sections", [])
    if not sections:
        return {}

    content = _generate_page_test(section_map, framework)
    if not content:
        return {}

    return {"Page.test.tsx": content}
