# Common Frontend Pitfalls

> Deep-dive reference for frequently encountered build errors and their canonical fixes.
> Source: typescript-cheatsheets/react (47K⭐) + awesome-cursorrules (39K⭐) + AGIX smoke test history

## JSX Entity Escaping (react/no-unescaped-entities)

**Error**: `react/no-unescaped-entities` — `"` can be escaped with `&quot;`, but this is NOT correct in JSX!

**Root Cause**: JSX is NOT HTML. HTML entity escaping (`&quot;`, `&apos;`, `&lt;`, `&gt;`) does NOT work in JSX text content. ESLint will reject both the raw character AND the HTML entity, creating an infinite fix loop.

```tsx
// ❌ WRONG — Raw special characters in JSX text
<p>Click "here" to start</p>

// ❌ STILL WRONG — HTML entities don't work in JSX
<p>Click &quot;here&quot; to start</p>

// ✅ CORRECT — JavaScript expression syntax
<p>Click {'"'}here{'"'} to start</p>

// ✅ ALSO CORRECT — Use template literals
<p>{`Click "here" to start`}</p>

// ✅ ALSO CORRECT — Use curly quotes (typographically better)
<p>Click \u201chere\u201d to start</p>
```

**Escaping rules**:
| Character | ❌ Wrong (HTML entity) | ✅ Correct (JSX) |
|-----------|----------------------|-----------------|
| `"` | `&quot;` | `{'"'}` or `` {`"`} `` |
| `'` | `&apos;` | `{"'"}` or `` {`'`} `` |
| `<` | `&lt;` | `{'<'}` |
| `>` | `&gt;` | `{'>'}` |
| `{` | N/A | `{'{'}` |
| `}` | N/A | `{'}'}` |

**When ESLint flags `no-unescaped-entities`**: ALWAYS use `{'char'}` syntax, NEVER `&entity;`.

## Next.js 14.2.x Internal Prerendering Build Error (LOOP HAZARD)

**Error**: `<Html> should not be imported outside of pages/_document` during `npm run build`

**CRITICAL**: If `rg 'next/document' src/` returns **nothing**, this is NOT a user code problem. This is a Next.js 14.2.x framework behavior where internal Pages Router fallback pages (`_error.js`, `_document.js`) are generated in `.next/server/pages/` during static prerendering — even in pure App Router projects.

**DO NOT** loop searching for `next/document` in user code. The import is inside `node_modules/next/dist/pages/_error.js`.

**Fix** — update `next.config.mjs`:
```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',  // Avoids Pages Router fallback generation
  experimental: {
    missingSuspenseWithCSRBailout: false,
  },
};
export default nextConfig;
```

If `output: 'standalone'` is not desired, the alternative is to ensure ALL App Router error pages exist:
- `src/app/not-found.tsx` (handles 404)
- `src/app/error.tsx` (handles 500, must have `'use client'`)
- `src/app/global-error.tsx` (handles root layout errors, must have `'use client'`)

All three must use `'use client'` directive and must NOT import from `next/document`.


## HTML Intrinsic Attribute Conflicts

**Error**: `Property 'size' does not exist on type 'IntrinsicAttributes & ButtonProps'`

**Root Cause**: Custom prop name collides with native HTML attribute.

```tsx
// ❌ WRONG — 'size' is a native number attribute on <button>
interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  size?: 'sm' | 'md' | 'lg';
}

// ✅ FIX — Use Omit<> to remove the conflicting native attribute
interface ButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'size'> {
  size?: 'sm' | 'md' | 'lg';
}
```

**Commonly conflicting props**: `size`, `color`, `width`, `height`, `form`, `content`, `title`, `label`, `name`.

## Hydration Mismatch Errors

**Error**: `Hydration failed because the initial UI does not match what was rendered on the server`

**Causes**:
1. Using `Date.now()`, `Math.random()`, or browser-only APIs during render
2. Conditional rendering based on `window` or `document`
3. Browser extensions injecting elements

```tsx
// ❌ WRONG — different output on server vs client
function Time() {
  return <span>{new Date().toLocaleTimeString()}</span>;
}

// ✅ FIX — use useEffect for client-only values
function Time() {
  const [time, setTime] = useState('');
  useEffect(() => { setTime(new Date().toLocaleTimeString()); }, []);
  return <span>{time}</span>;
}
```

## Module Not Found Errors

**Error**: `Module not found: Can't resolve 'lucide-react'`

**Root Cause**: Package not installed, or using wrong import path.

**Fix checklist**:
1. Verify the package is in `package.json` dependencies
2. Run `npm install` (not just import the module)
3. Check exact package name (e.g., `lucide-react` not `lucide`)
4. For component libraries (shadcn), run the add command before importing

## Object is Possibly Null/Undefined

**Error**: `Object is possibly 'null'` / `Object is possibly 'undefined'`

```tsx
// ❌ WRONG — direct access without guard
const name = user.name; // TS2531 if user can be null

// ✅ FIX — optional chaining
const name = user?.name;

// ✅ FIX — null check
if (user) { const name = user.name; }

// ✅ FIX — nullish coalescing
const name = user?.name ?? 'Anonymous';
```

## useEffect Cleanup Pitfalls

```tsx
// ❌ WRONG — arrow function implicitly returns setTimeout's number
useEffect(() =>
  setTimeout(() => { /* ... */ }, 1000),
  []
);

// ✅ FIX — wrap in curly braces so nothing is returned
useEffect(() => {
  const timer = setTimeout(() => { /* ... */ }, 1000);
  return () => clearTimeout(timer);
}, []);
```

## TypeScript Strict Mode Common Errors

**`noUncheckedIndexedAccess`**: Array/object index access returns `T | undefined`.

```tsx
const items = ['a', 'b', 'c'];
// ❌ items[0].toUpperCase() — possibly undefined
// ✅ items[0]?.toUpperCase() — safe

const map: Record<string, number> = {};
// ❌ map['key'] + 1 — possibly undefined
// ✅ (map['key'] ?? 0) + 1 — safe
```

## Import Path Gotchas

```tsx
// ❌ WRONG — relative paths break when files move
import { Button } from '../../../components/ui/Button';

// ✅ CORRECT — use path aliases (configured in tsconfig.json)
import { Button } from '@/components/ui/Button';
```

Ensure `tsconfig.json` has:
```json
{
  "compilerOptions": {
    "paths": { "@/*": ["./src/*"] }
  }
}
```

## React Key Prop

```tsx
// ❌ WRONG — using array index as key for dynamic lists
{items.map((item, i) => <Card key={i} {...item} />)}

// ✅ CORRECT — use stable, unique identifier
{items.map((item) => <Card key={item.id} {...item} />)}
```
