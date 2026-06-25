# Build Workflow Patterns — Build KB

Pre-build checklists, type-checking workflows, and build optimization patterns for Next.js / TypeScript projects.

## Pre-Build Checklist

Run these checks BEFORE `npm run build` to catch errors early and avoid build-loop death spirals.

### Step 1: Type Check (MANDATORY)
```bash
# Next.js 15.5+ (recommended)
npx next typegen && npx tsc --noEmit

# Older Next.js / Generic TypeScript
npx tsc --noEmit
```

**Why:** `next build` runs TypeScript checking as part of the build. If types fail at build time, you waste the entire build cycle. Running `tsc --noEmit` first catches type errors in seconds instead of minutes.

### Step 2: Lint Check
```bash
npx next lint
# OR
npx eslint src/ --ext .ts,.tsx
```

### Step 3: Dependency Audit
```bash
# Ensure all dependencies are installed
npm install

# Check for peer dependency issues
npm ls 2>&1 | grep "ERESOLVE\|peer dep\|missing"
```

### Step 4: Environment Variables
```bash
# Verify required env vars exist
# For Next.js, client-side vars MUST have NEXT_PUBLIC_ prefix
env | grep -E "DATABASE_URL|NEXT_PUBLIC_"
```

## Next.js 15.5 Type Generation

Next.js 15.5 introduced `next typegen` — generates route types without running the full dev server or build.

```bash
# Generate types for all routes, then validate
next typegen && tsc --noEmit

# CI pipeline example
next typegen && npm run type-check
```

### What `next typegen` generates:
- `PageProps` — typed params/searchParams for each page
- `LayoutProps` — typed params for each layout
- `RouteContext` — typed params for route handlers
- All types are globally available (no imports needed)

### Route Export Validation
Next.js 15.5+ validates route exports at build time:
```typescript
// This will cause a build error if 'dynamic' value is invalid
export const dynamic = 'force-dynamic'  // Valid: 'auto' | 'force-dynamic' | 'error' | 'force-static'
export const revalidate = 3600          // Valid: number | false
```

## Build Optimization

### Next.js Standalone Output
For Docker/containerized deployments, use standalone output to dramatically reduce image size:

```javascript
// next.config.ts
const nextConfig = {
  output: 'standalone',  // Creates self-contained build
  // Reduces build memory and size
}
```

**Result:** Build output goes from ~500MB to ~50MB, and the server runs with `node server.js` instead of requiring the full `node_modules`.

### Memory Management
Large Next.js builds can exhaust Node.js heap memory:

```bash
# Increase heap before building
NODE_OPTIONS='--max-old-space-size=4096' npm run build

# For Docker builds
ENV NODE_OPTIONS='--max-old-space-size=4096'
```

### Kill Orphan Processes
Orphan node/next processes consume memory and file watchers:
```bash
# Kill all orphan processes before building
pkill -f 'node|next' || true
rm -rf node_modules/.cache .next/cache /tmp/next-*
```

## TypeScript Strict Mode Patterns

### Gradual Strict Mode Adoption
```json
// tsconfig.json — start lenient, tighten over time
{
  "compilerOptions": {
    "strict": false,          // Start here
    "noImplicitAny": true,    // Enable first
    "strictNullChecks": true, // Enable second
    "strictFunctionTypes": true // Enable third
  }
}
```

### Common Type Errors During Build

| Error | Fix |
|-------|-----|
| `Parameter implicitly has 'any' type` | Add explicit type: `(e: Error)`, `(data: unknown)` |
| `Object is possibly 'null'` | Add null guard: `if (!obj) return;` or `obj?.property` |
| `Type X is not assignable to type Y` | Check interface consistency, use `Partial<T>` for optional fields |
| `Property does not exist on type` | Add to interface or use type assertion: `(obj as ExtendedType).prop` |

## CI/CD Build Pipeline

### Recommended Order
```yaml
# 1. Install dependencies (cached)
- npm ci

# 2. Generate Prisma client (if using Prisma)
- npx prisma generate

# 3. Type check (fast, catches most errors)
- npx next typegen && npx tsc --noEmit

# 4. Lint
- npx next lint

# 5. Test
- npm test

# 6. Build (only after all checks pass)
- npm run build
```

This order ensures the cheapest checks run first, preventing wasted build time.
