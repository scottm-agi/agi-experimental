# CSS & Design Systems Patterns

> Deep-dive reference for CSS architecture, design tokens, and Tailwind patterns.
> Source: awesome-cursorrules (39K⭐)

## CSS Custom Properties (Design Tokens)

```css
/* ✅ Define tokens in globals.css :root */
:root {
  --color-primary: #3b82f6;
  --color-secondary: #8b5cf6;
  --color-background: #0a0a0f;
  --color-surface: #1a1a2e;
  --color-text: #e2e8f0;
  --radius-sm: 0.375rem;
  --radius-md: 0.75rem;
  --radius-lg: 1rem;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.1);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.15);
  --transition-fast: 150ms ease;
  --transition-normal: 300ms ease;
}

/* ✅ Dark mode via media query or class */
@media (prefers-color-scheme: dark) {
  :root { --color-background: #0a0a0f; }
}
.dark { --color-background: #0a0a0f; }
```

## Tailwind v3 vs v4 Differences

| Feature | Tailwind v3 | Tailwind v4 |
|---------|------------|------------|
| Config | `tailwind.config.js` | CSS-based `@theme` |
| Content | `content: ['./src/**/*.{ts,tsx}']` | Auto-detection |
| Plugins | `plugins: [require('...')]` | `@plugin` directive |
| Imports | `@tailwind base/components/utilities` | `@import 'tailwindcss'` |

**Always check `package.json` for the installed version before configuring.**

## Responsive Design — Mobile First

```css
/* ✅ Mobile-first breakpoints */
.container { padding: 1rem; }                    /* Mobile */
@media (min-width: 640px) { .container { padding: 1.5rem; } }  /* sm */
@media (min-width: 768px) { .container { padding: 2rem; } }    /* md */
@media (min-width: 1024px) { .container { padding: 3rem; } }   /* lg */
@media (min-width: 1280px) { .container { max-width: 1200px; } } /* xl */
```

## Glassmorphism Pattern

```css
.glass-card {
  background: rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: var(--radius-md);
}
```

## Z-Index Scale

```css
/* ✅ Use a defined scale — never arbitrary z-index values */
:root {
  --z-dropdown: 10;
  --z-sticky: 20;
  --z-fixed: 30;
  --z-modal-backdrop: 40;
  --z-modal: 50;
  --z-popover: 60;
  --z-tooltip: 70;
}
```

## Animation Best Practices

```css
/* ✅ Respect motion preferences */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

/* ✅ Use transform/opacity for smooth 60fps animations */
.card-hover {
  transition: transform var(--transition-normal), box-shadow var(--transition-normal);
}
.card-hover:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-md);
}
```

## Font Loading

```tsx
// ✅ Next.js font optimization
import { Inter } from 'next/font/google';
const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });

// In layout.tsx
<html className={inter.variable}>
```

```css
/* Reference in CSS */
body { font-family: var(--font-inter), system-ui, sans-serif; }
```
