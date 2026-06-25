# Next.js App Router Rules — Mandatory for All Next.js Projects

These rules prevent common build failures in Next.js App Router projects. Follow them EXACTLY.

---

## 1. `Html` is NOT a valid import

❌ **NEVER** import `Html`, `Head`, `Main`, or `NextScript` from `next/document`.
These are Pages Router components and DO NOT EXIST in App Router.

✅ Use `layout.tsx` with standard JSX `<html>`, `<head>`, `<body>` tags instead:
```tsx
// src/app/layout.tsx
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
```

## 2. `useContext` Requires `'use client'`

❌ **NEVER** use `useContext`, `useState`, `useEffect`, or any React hook in a Server Component.

✅ Add `'use client'` directive at the TOP of any file using hooks:
```tsx
'use client'
import { useContext, useState } from 'react'
```

## 3. `getServerSideProps` / `getStaticProps` DO NOT EXIST in App Router

❌ **NEVER** use `getServerSideProps`, `getStaticProps`, or `getStaticPaths`.

✅ Use async Server Components with `fetch()` or direct DB access:
```tsx
// src/app/blog/[slug]/page.tsx
export default async function BlogPost({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  const post = await fetchPost(slug)
  return <article>{post.title}</article>
}
```

## 4. Dynamic Route Params are ASYNC in Next.js 15+

❌ **NEVER** destructure `params` directly in page/layout props.

✅ **ALWAYS** `await` the params object:
```tsx
// ❌ WRONG:
export default function Page({ params: { id } }) { ... }

// ✅ CORRECT:
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  ...
}
```

This applies to `searchParams` as well:
```tsx
export default async function Page({ searchParams }: { searchParams: Promise<{ q?: string }> }) {
  const { q } = await searchParams
}
```

## 5. API Routes Use `route.ts` NOT `api/xxx.ts`

❌ **NEVER** create API files like `pages/api/contact.ts`.

✅ Create route handlers at `src/app/api/<endpoint>/route.ts`:
```tsx
// src/app/api/contact/route.ts
import { NextResponse } from 'next/server'

export async function POST(request: Request) {
  const body = await request.json()
  return NextResponse.json({ ok: true })
}
```

## 6. Metadata Export (NOT `<Head>`)

❌ **NEVER** use `<Head>` from `next/head` in App Router.

✅ Export `metadata` or use `generateMetadata`:
```tsx
// Static metadata
export const metadata = {
  title: 'My Page',
  description: 'Page description',
}

// Dynamic metadata
export async function generateMetadata({ params }) {
  return { title: `Post: ${(await params).slug}` }
}
```

## 7. Image Optimization

✅ Always use `next/image` for images:
```tsx
import Image from 'next/image'
<Image src="/hero.jpg" alt="Hero" width={1200} height={600} priority />
```

For external images, add domains to `next.config.js`:
```js
images: {
  remotePatterns: [
    { protocol: 'https', hostname: '*.example.com' },
  ],
}
```

## 8. `'use server'` for Server Actions

Server Actions (form submissions, mutations) use `'use server'`:
```tsx
// In a Server Component or separate file:
async function submitForm(formData: FormData) {
  'use server'
  const email = formData.get('email')
  // Process on server...
}

// In JSX:
<form action={submitForm}>...</form>
```

## 9. Loading & Error Boundaries

Use file-convention components for loading/error states:
- `loading.tsx` — Shown while route loads
- `error.tsx` — Shown on runtime errors (MUST be `'use client'`)
- `not-found.tsx` — Shown for 404s (use `notFound()` to trigger)

## 10. Client vs Server Component Decision

| Need | Component Type | Directive |
|------|---------------|-----------|
| Hooks (useState, useEffect) | Client | `'use client'` |
| Event handlers (onClick) | Client | `'use client'` |
| Browser APIs (localStorage) | Client | `'use client'` |
| Fetch data, DB access | Server | (default) |
| Access env vars (server-only) | Server | (default) |
| Render static HTML | Server | (default) |

**Default is Server Component.** Only add `'use client'` when you NEED interactivity.
