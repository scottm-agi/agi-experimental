### generate_image

Generates high-quality images from text descriptions using AI (Google Gemini 3.1 Flash via OpenRouter by default).
This tool allows for visual creative tasks like logo design, illustrations, diagrams, or generating UI mockups.

The provider and model are **automatically** configured from Settings > Models > Image Generation.
Default: `google/gemini-3.1-flash-image-preview` on OpenRouter.

> **IMPORTANT**: Do NOT use `settings_set` to change `image_gen_model` or `image_gen_provider`.
> These are pre-configured by the user in the Settings UI. The tool reads them automatically.
> Changing these settings will be REJECTED by the settings_set validation.

**Supported models** (these are already configured — do NOT change them):
- OpenRouter: `google/gemini-3.1-flash-image-preview`
- Gemini Direct: `gemini-3.1-flash-image-preview`
- OpenAI/DALL-E: `dall-e-3`

Args:
- **prompt**: A detailed description of the image to generate. Be specific about style, objects, lighting, and composition.
- **instruction**: (optional) Domain-specific rules or style directives that get prepended to the prompt. Use this to inject design system rules, art direction, brand guidelines, or rendering constraints. The calling agent is responsible for filling this based on its domain — for example, a frontend agent passes browser-screenshot rendering rules, while a content agent might pass brand illustration guidelines. When provided, this is prepended to the prompt automatically.
- **reference_image**: (optional) **Image Context Chain.** Path to a previously generated image to use as visual style reference. The tool will include this image as context in the API call, ensuring the generated image matches the reference's colors, typography, layout style, and visual language. Use this to maintain visual consistency across multi-image sequences. Path can be absolute or relative to the active project directory.
- **output_path**: (optional) Path within the active project where the image should be saved. Can be relative (e.g., `public/images/hero.png`) or absolute within the project. If omitted, the image is saved to the project's tmp folder. **Do NOT use code_execution_tool to `mv` images — use this argument instead.**
- **aspect_ratio**: (optional) Aspect ratio for the image. Options: "1:1" (default), "16:9", "9:16", "4:3", "3:4". Only used with Gemini direct provider.

---

#### 🔴 MANDATORY Rendering Rules for UI Mockups

When generating UI mockups (landing pages, dashboards, audit pages, settings pages, etc.), your `instruction` and `prompt` **MUST** follow these rules. NEVER contradict them:

1. **NO BROWSER FRAMES**: Never include browser chrome, address bars, tab bars, device frames, phone bezels, or any surrounding UI. The generated image IS the page — content must touch ALL FOUR EDGES, edge-to-edge, zero empty space around it.
2. **DESIGN SYSTEM CONSISTENCY**: If a `00-design-system.png` has been generated, ALL subsequent page mockups MUST match its color palette, typography, and component styles. NEVER switch from dark to light mode (or vice versa) unless the design system explicitly defines both modes.
3. **ALWAYS use `reference_image`**: When generating page mockups after the design system card, ALWAYS pass `reference_image` pointing to `00-design-system.png`. This is MANDATORY, not optional.
4. **NEVER override the design system colors in your prompt**: If the design system uses `#0a0a0f` background, do NOT write "white background" or "light mode" in ANY page mockup prompt. Extract colors FROM the design system.
5. **Straight-on perspective ONLY**: Generate images as if viewed head-on, not at an angle, not in perspective, not as a photograph of a screen.

**Example of a CORRECT instruction for UI mockups:**
```
Generate a full-page screenshot of a web page. Content MUST touch ALL FOUR EDGES. ZERO borders, ZERO margins. NO device frames, NO browser chrome, NO perspective. Match the colors, typography, and component styles from the reference image EXACTLY. Straight-on view only.
```

**Example of an INCORRECT instruction (DO NOT DO THIS):**
```
Generate a photorealistic screenshot that looks like a real browser capture.  ← WRONG: adds browser chrome
The background should be clean and light to contrast with the dashboard.     ← WRONG: contradicts dark design system
```

---

#### 🎨 Image Context Chain Pipeline (MANDATORY for multi-image sequences)

When generating multiple related images (e.g., design system + screen mockups), you **MUST** follow this pipeline to ensure visual consistency:

1. **Image 0 — Theme/Design System Panel**: Generate standalone (no `reference_image`). This establishes the visual language (colors, typography, spacing, component styles).
2. **Image 1 — First Full Mockup**: Generate with `reference_image` pointing to Image 0 (the theme panel). This is the primary screen mockup.
3. **Images 2+ — Additional Screens**: Generate with `reference_image` pointing to **Image 1** (the first full mockup). This ensures all screens share the same design language established by the first mockup.

The `reference_image` path is returned in every success response under `PATH:`, so you can chain it into the next call.

---

usage:

~~~json
{
    "thoughts": [
        "User wants a hero image for their landing page...",
        "I'll save it directly to public/images/ using output_path.",
    ],
    "headline": "Generating hero image for landing page",
    "tool_name": "generate_image",
    "tool_args": {
        "prompt": "A modern, minimalist hero image for a SaaS landing page, abstract gradient background with floating geometric shapes, vibrant purple and blue tones, high resolution.",
        "output_path": "public/images/hero.png",
        "aspect_ratio": "16:9"
    }
}
~~~

**With instruction (UI mockup example):**

~~~json
{
    "thoughts": [
        "Need a landing page mockup — I'll pass the STYLE_PREFIX instruction for browser-screenshot quality.",
    ],
    "headline": "Generating landing page mockup",
    "tool_name": "generate_image",
    "tool_args": {
        "instruction": "Generate an image that looks EXACTLY like a full-page screenshot captured by pressing Cmd+Shift+S in a web browser. Content MUST touch ALL FOUR EDGES. ZERO empty space, ZERO border, ZERO margin. NO device mockups, NO browser chrome, NO perspective. The image IS the webpage.",
        "prompt": "A premium SaaS landing page, dark mode on #0a0a0f background. Glass navbar with logo and CTA. Hero section with bold headline. Features grid with 3 glassmorphism cards. Pricing with 3 tiers. Dark footer.",
        "output_path": "tmp/design-mockups/01-landing.png",
        "aspect_ratio": "16:9"
    }
}
~~~

**Image Context Chain (multi-screen consistency):**

~~~json
{
    "thoughts": [
        "I generated the theme panel at tmp/design-mockups/00-design-system.png.",
        "Now I need the landing page — I'll use the theme panel as reference_image so it matches the design system."
    ],
    "headline": "Generating landing page mockup with design system context",
    "tool_name": "generate_image",
    "tool_args": {
        "instruction": "Generate an image that looks EXACTLY like a full-page screenshot. Match the colors, typography, spacing, and component styles from the reference image EXACTLY.",
        "prompt": "A premium SaaS landing page with deep navy (#0f172a) background. Glass navbar. Hero section with bold Inter font headline. Features grid with 3 cards using the color palette from the reference. Pricing section. Footer.",
        "reference_image": "tmp/design-mockups/00-design-system.png",
        "output_path": "tmp/design-mockups/01-landing.png",
        "aspect_ratio": "16:9"
    }
}
~~~
