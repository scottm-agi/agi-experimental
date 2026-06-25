---
name: "ui-design-first"
description: "Design-first UI workflow: generate photorealistic mockups of every screen using generate_image before writing any code, then use mockups as visual alignment targets to ensure premium, consistent UI/UX across the entire application."
version: "1.0.0"
author: "AGIX Team"
tags: ["frontend", "design", "mockup", "ui-ux", "image-generation", "consistency", "quality"]
trigger_patterns:
  - "build frontend"
  - "create landing page"
  - "build UI"
  - "design website"
  - "create web app"
  - "frontend development"
  - "build pages"
  - "implement UI"
required_agents:
  - frontend    # Executes the design-first pipeline and builds the code
  - architect   # Provides the spec with page map and design direction
---

# UI Design-First Workflow

Generate photorealistic image mockups of every UI screen **before writing any code**, then use those mockups as visual alignment targets during implementation. This ensures theme consistency, premium aesthetics, and design coherence across the entire application.

## Why Design-First?

Building UI code without a visual target leads to:
- Inconsistent spacing, typography, and color across pages
- Generic-looking output that doesn't feel premium
- Wasted refactoring cycles to fix visual issues after the fact

By generating mockups first, the frontend agent has a **concrete visual contract** to code against — just like a human developer working from Figma designs.

## The 7-Step Pipeline

Execute these steps **in order**. Do NOT skip any step.

### Step 1: Gather Design Context 🎨

Extract design direction from the architect's specification and any user-provided brand guidelines. Identify:

- **Color Palette**: Primary, secondary, accent, background, surface, text colors
- **Typography**: Font families (e.g., Inter, Outfit, Roboto), heading/body sizes, weights
- **Visual Mood**: Dark mode, light mode, glassmorphism, gradients, minimal, bold
- **Brand Elements**: Logo, brand name, tagline, any provided assets
- **Design References**: Any referenced sites (Linear, v0.dev, Apple, etc.)
- **Layout Pattern**: SaaS landing, dashboard, e-commerce, portfolio, documentation

If the architect spec doesn't include explicit design direction, establish sensible defaults:
- Dark mode with vibrant accent colors (HSL-tuned)
- Inter or similar modern sans-serif
- Glassmorphism cards with subtle backdrop-blur
- Smooth gradients and micro-animations

### Step 2: Generate Design System Reference Card 🎯

Create a single "design system card" mockup that establishes the visual foundation:

```json
{
    "tool_name": "generate_image",
    "tool_args": {
        "prompt": "A professional UI design system reference card showing: color palette swatches (dark background #0a0a0f, primary blue #3b82f6, accent purple #8b5cf6, success green #22c55e, surface #1a1a2e with glass effect), typography samples in Inter font (headings bold, body regular), button styles (primary filled, secondary outline, ghost), card components with glassmorphism effect (backdrop-blur, border-opacity), spacing scale, and icon style examples. Clean layout on dark background, presentation quality, no device frames.",
        "aspect_ratio": "16:9"
    }
}
```

Save this to `tmp/design-mockups/00-design-system.png`. Reference this card while generating all subsequent page mockups to maintain consistency.

### Step 3: Generate Per-Page Mockups 📸

For **each page** in the architect's page map, generate a photorealistic mockup. The prompt must be highly detailed and reference the design system from Step 2.

**Prompt Engineering Rules:**
1. Describe the page as if it were a **screenshot of a real, finished, production website** — not a wireframe
2. Include specific colors from your design system (hex values)
3. Specify typography (font name, weights, sizes)
4. Describe every section from top to bottom: navbar, hero, features grid, pricing, footer, etc.
5. Include realistic content — real-looking text, realistic data in charts, plausible company names
6. Specify the visual effects: glassmorphism, gradients, shadows, hover states
7. Use `aspect_ratio: "16:9"` for desktop views

**Example — Landing Page mockup:**
```json
{
    "tool_name": "generate_image",
    "tool_args": {
        "prompt": "A premium SaaS landing page screenshot for 'Acme Analytics', dark mode design on #0a0a0f background. Floating navigation bar with frosted glass effect (backdrop-blur, bg-black/40, rounded-2xl) showing logo, nav links (Features, Pricing, About), and a glowing blue CTA button 'Get Started'. Hero section with large bold Inter font headline 'Analytics That Drive Growth' in white, subheadline in gray #94a3b8, gradient blue-to-purple CTA button, and a floating dashboard preview card with glassmorphism effect showing a line chart. Features grid section with 3 cards having glass borders and colored icon badges (blue, purple, green). Stats bar showing '10K+ Users', '99.9% Uptime', '50M+ Events'. Pricing section with 3 tier cards (Starter $9, Pro $29, Enterprise Custom) with the Pro card highlighted with a blue glow border. Dark footer with company links and social icons. Modern, clean, premium aesthetic similar to Linear or Vercel websites. No device frames, just the page content.",
        "aspect_ratio": "16:9"
    }
}
```

**Naming convention:** `tmp/design-mockups/01-landing.png`, `tmp/design-mockups/02-dashboard.png`, etc.

**Generate mockups for ALL pages** in the spec before proceeding to Step 4.

### Step 4: Cross-Page Consistency Audit ✅

After generating all mockups, review them together. Check for:

| Check | What to Verify |
|-------|---------------|
| **Navigation** | Same navbar style, links, and positioning on every page |
| **Color Palette** | Same primary, accent, background colors across all pages |
| **Typography** | Same font family, heading sizes, and body text styles |
| **Spacing** | Consistent padding, margins, and section spacing |
| **Component Style** | Cards, buttons, badges look identical across pages |
| **Footer** | Same footer on every page |
| **Dark/Light Mode** | Consistent mode treatment across all pages |
| **Visual Density** | Pages feel like they belong to the same product |

**If any page looks inconsistent**, regenerate its mockup with an updated prompt that explicitly references the design system and the other pages. Continue until all pages feel like they belong to the same product.

### Step 5: Extract Asset Requirements 🖼️

Review each mockup and identify custom visual assets needed:

- **Hero images/illustrations**: Background graphics, abstract shapes, product screenshots
- **Icons**: If the mockup shows specific icons, note which icon library to use (Lucide, Heroicons)
- **Logos**: Company logo if not provided
- **Background patterns**: Gradient meshes, dot grids, abstract blobs
- **Feature illustrations**: Custom SVGs or images for feature cards

Create a list of required assets in the project's `tmp/design-mockups/asset-requirements.md`.

### Step 6: Generate Required Assets 🎨

Use `generate_image` to create any custom graphics identified in Step 5:

```json
{
    "tool_name": "generate_image",
    "tool_args": {
        "prompt": "Abstract gradient mesh background, dark purple and blue tones (#1a1a2e to #3b82f6), soft organic shapes, suitable as a hero section background for a SaaS website, subtle and professional, high resolution, seamless edges",
        "aspect_ratio": "16:9"
    }
}
```

Save assets to `tmp/design-mockups/assets/`. These will be moved to `public/` or `src/assets/` during implementation.

### Step 7: Code with Mockup Alignment 🔧

Now write the actual code, using the mockups as your visual contract:

1. **Start with the design system** — translate the mockup colors, fonts, and spacing into CSS variables in `globals.css`
2. **Build shared components first** — Navbar, Footer, Button, Card components that appear across pages
3. **Implement page by page** — For each page:
   a. Open the corresponding mockup for reference
   b. Code the page matching the mockup's layout, colors, and content structure
   c. Run the dev server and use `browser_agent` to take a screenshot
   d. Compare the screenshot to the mockup — fix any visual drift
4. **Final consistency check** — After all pages are built, browse through every page and verify the live site matches the mockup set

## Prompt Templates

### Landing Page
```
A premium [industry] landing page screenshot, [dark/light] mode, [color scheme]. Floating glass navbar with [brand name] logo, nav links [list], CTA button. Hero with bold headline '[headline]', subheadline, gradient CTA, floating product preview. Features grid with [N] cards, glass borders, colored icons. Social proof section with stats/logos. Pricing with [N] tiers, highlighted recommended plan. Dark footer with links and social. Premium aesthetic like [reference]. No frames.
```

### Dashboard
```
A premium web dashboard screenshot for [app name], [dark/light] mode. Left sidebar navigation with icon+label menu items, active state highlighted. Top bar with search, notifications bell, user avatar. Main content: [describe widgets — stat cards, charts, tables, activity feeds]. Cards with glassmorphism, subtle shadows. Data visualizations with [color scheme] palette. Clean grid layout, 16px spacing. No device frames.
```

### Auth Page (Login/Signup)
```
A premium [login/signup] page screenshot, [dark/light] split layout. Left side: branded panel with [gradient/illustration], company logo, tagline. Right side: clean form with email/password inputs, [social login buttons], submit CTA, toggle link to [signup/login]. Input fields with subtle borders, focus glow effect. Premium aesthetic, centered vertically. No device frames.
```

## Anti-Patterns

1. ❌ **Coding without mockups** — Every page must have a mockup before code is written
2. ❌ **Vague image prompts** — "a nice landing page" produces garbage. Be specific about colors, layout, content
3. ❌ **Skipping the consistency audit** — Individual pages may look great but clash with each other
4. ❌ **Ignoring the mockup during coding** — The mockup is your contract, not a suggestion
5. ❌ **Using placeholder images** — Use `generate_image` for ALL visual assets
6. ❌ **Generating mockups but not saving them** — Always save to `tmp/design-mockups/` for reference

## Quality Gate

Before moving from design to code, ALL of these must be true:

| Gate | Criteria |
|------|----------|
| Mockup Count | 1 mockup per page in the spec |
| Design System | `00-design-system.png` exists |
| Consistency | All mockups share nav, footer, palette, typography |
| Asset List | `asset-requirements.md` exists |
| Assets Generated | All required custom graphics created |
