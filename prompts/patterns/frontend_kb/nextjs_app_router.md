# Next.js App Router Patterns

> Deep-dive reference for Next.js App Router conventions and gotchas.
> Source: awesome-cursorrules (39K⭐) + typescript-cheatsheets/react

## Server vs Client Components

**Default is Server Component.** Only add `'use client'` when you need:
- React hooks (`useState`, `useEffect`, `useContext`, etc.)
- Event handlers (`onClick`, `onChange`, etc.)
- Browser APIs (`window`, `document`, `localStorage`)

```tsx
// ✅ Server Component (default) — no directive needed
export default function Page() {
  return <h1>Static Content</h1>;
}

// ✅ Client Component — directive on line 1
'use client';
import { useState } from 'react';
export default function Counter() {
  const [count, setCount] = useState(0);
  return <button onClick={() => setCount(c => c + 1)}>{count}</button>;
}
```

## Layout vs Page vs Template

| File | Purpose | Re-renders? |
|------|---------|-------------|
| `layout.tsx` | Shared UI wrapper, persists across navigation | No |
| `page.tsx` | Route-specific content | Yes |
| `template.tsx` | Like layout but re-renders on navigation | Yes |
| `loading.tsx` | Suspense fallback for the page | Auto |
| `error.tsx` | Error boundary for the segment | Auto |
| `not-found.tsx` | 404 handler | Auto |

## NEVER Use Pages Router APIs in App Router

```tsx
// ❌ WRONG — Pages Router only
import { Html, Head, Main, NextScript } from 'next/document';
import { useRouter } from 'next/router'; // Pages Router hook

// ✅ CORRECT — App Router equivalents
import { useRouter, usePathname, useSearchParams } from 'next/navigation';
// Layout handles <html> and <body> directly
```

## Metadata API

```tsx
// ✅ Static metadata (server components only)
export const metadata = {
  title: 'My Page',
  description: 'Page description',
};

// ✅ Dynamic metadata
export async function generateMetadata({ params }) {
  return { title: `Post ${params.id}` };
}
```

## Data Fetching Patterns

```tsx
// ✅ Server Component — fetch directly
export default async function Page() {
  const data = await fetch('https://api.example.com/data');
  return <div>{/* render data */}</div>;
}

// ✅ Client Component — use useEffect or SWR/TanStack Query
'use client';
import useSWR from 'swr';
export default function ClientPage() {
  const { data } = useSWR('/api/data', fetcher);
  return <div>{/* render data */}</div>;
}
```

## Dynamic Imports for Client-Only Libraries

```tsx
// ✅ Disable SSR for client-only components (maps, charts, editors)
import dynamic from 'next/dynamic';
const Map = dynamic(() => import('./Map'), { ssr: false });
const Chart = dynamic(() => import('./Chart'), {
  ssr: false,
  loading: () => <div>Loading chart...</div>,
});
```

## Route Handlers (API Routes in App Router)

```tsx
// app/api/users/route.ts
import { NextResponse } from 'next/server';

export async function GET(request: Request) {
  return NextResponse.json({ users: [] });
}

export async function POST(request: Request) {
  const body = await request.json();
  return NextResponse.json({ created: body }, { status: 201 });
}
```

## Image Optimization

```tsx
// ✅ Always use next/image for optimized loading
import Image from 'next/image';
<Image src="/hero.jpg" alt="Hero" width={1200} height={600} priority />

// ❌ Never use raw <img> tags in Next.js
<img src="/hero.jpg" />
```

## Environment Variables

```
# .env.local
DATABASE_URL=...           # Server-only (default)
NEXT_PUBLIC_API_URL=...    # Exposed to client (NEXT_PUBLIC_ prefix required)
```

Server components can access all env vars. Client components can ONLY access `NEXT_PUBLIC_*` vars.

## Build Configuration

**CRITICAL: Always set `NODE_ENV=production` when running builds.**

```bash
# ✅ CORRECT — always set NODE_ENV for builds
NODE_ENV=production npm run build

# ❌ WRONG — omitting NODE_ENV causes inconsistent build behavior
npm run build
```

**next.config.mjs guidelines:**
- Only use `experimental` options compatible with your Next.js version
- `missingSuspenseWithCSRBailout` is Next.js 14.x+ only — do NOT use with 13.x
- Always include `eslint: { ignoreDuringBuilds: true }` and `typescript: { ignoreBuildErrors: true }` for initial scaffolding

**Build failure recovery:**
1. First, check for `NODE_ENV` issues: `NODE_ENV=production npm run build`
2. Remove invalid `experimental` options from `next.config.mjs`
3. Clear cache: `rm -rf .next/cache node_modules/.cache` then rebuild (or use `services_mgt action='restart_service'`)
4. Do NOT downgrade Next.js versions unless the error is version-specific
5. Do NOT create `pages/` directory stubs — this conflicts with App Router

## Route Diagnostics (404 Troubleshooting)

When encountering HTTP 404 errors on Next.js App Router routes:

1. **CLEAR `.next` CACHE FIRST**: Run `rm -rf .next/cache node_modules/.cache && npm run dev` (or use `services_mgt action='restart_service'`). Stale `.next` cache after file writes is the #1 cause of false 404s.
2. **NEVER move files out of route groups**: Directories like `(app)`, `(auth)`, `(marketing)` are **App Router route groups** — they are architectural decisions, NOT bugs. Moving `(app)/dashboard/page.tsx` to `dashboard/page.tsx` BREAKS the layout hierarchy.
3. **NEVER rename or delete route group directories** (anything in parentheses like `(group)`).
4. **Verify the file actually exists**: Run `ls -la src/app/(group)/route/page.tsx` before concluding a route is missing. The file may exist inside a route group you're not seeing.
5. **Check `layout.tsx` chain**: Every route group can have its own `layout.tsx`. Moving a page out of its group orphans it from that layout's providers, auth wrappers, and navigation.

### Common 404 Root Causes
| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| 404 after creating new `page.tsx` | Stale `.next` cache | `rm -rf .next/cache node_modules/.cache && npm run dev` |
| 404 on `/dashboard` | File is at `(app)/dashboard/page.tsx` | Don't move it — route groups are transparent in URLs |
| 404 after restructuring folders | Broken `layout.tsx` chain | Restore route group structure |
| 404 on API route | File not named `route.ts` | Must be `app/api/*/route.ts` exactly |

## 🔴 Cross-Cutting Concerns (MANDATORY — ITR-45)

Every Next.js App Router project MUST include these files in the decomposition and implementation. These are NOT optional polish — they are required for production-quality applications.

### Required Files (per route group)

| File | Location | Purpose | When to Create |
|------|----------|---------|----------------|
| `error.tsx` | `app/error.tsx` + each route group | Error boundary — catches runtime errors | Phase 3 (Implementation) |
| `loading.tsx` | `app/loading.tsx` + each route group | Suspense fallback — shows during data fetching | Phase 3 (Implementation) |
| `not-found.tsx` | `app/not-found.tsx` (root only) | Custom 404 page | Phase 3 (Implementation) |

### Template: error.tsx
```tsx
'use client';
export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-screen">
      <h2 className="text-2xl font-bold mb-4">Something went wrong</h2>
      <p className="text-gray-600 mb-4">{error.message}</p>
      <button onClick={reset} className="px-4 py-2 bg-blue-600 text-white rounded">
        Try again
      </button>
    </div>
  );
}
```

### Template: loading.tsx
```tsx
export default function Loading() {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="animate-spin h-8 w-8 border-4 border-blue-600 border-t-transparent rounded-full" />
    </div>
  );
}
```

### Rule for Dashboard Pages
**All numeric values displayed in dashboards MUST come from database queries.** Never hardcode counts, averages, or statistics. Use Prisma `count()`, `aggregate()`, and `groupBy()` for dashboard stat cards. Hardcoded numbers like "Active: 12" or "Score: 88/100" are NEVER acceptable.
