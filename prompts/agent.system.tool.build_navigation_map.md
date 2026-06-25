## build_navigation_map
Scan a project's source files to produce a **Navigation Map** — a structured artifact listing all frontend routes, API endpoints, and navigation links.

This map serves as:
1. **E2E Test Plan** — the E2E agent reads it to know what pages and elements to test
2. **As-Designed vs As-Built Comparison** — diff what routes SHOULD exist vs what `curl` finds
3. **Route Coverage Report** — ensures no orphaned pages and no missing routes

### Parameters:
- **project_dir** (required): Absolute path to the project root directory
- **framework** (optional): Framework hint — `nextjs`, `vite`, `express`, `fastapi`, `flask`. Auto-detected from `package.json`/`requirements.txt` if omitted.

### When to use:
- **Before E2E testing** — generate the navigation map as the test plan
- **After implementing frontend pages** — verify all routes are discoverable
- **After implementing API endpoints** — verify all endpoints are registered
- **During review** — compare navigation map against curl results to find dead routes
- **When verifying completeness** — check that all spec'd routes were actually built

### What it scans:
- **Next.js**: `src/app/**/page.tsx` for pages, `src/app/api/**/route.ts` for APIs
- **Vite/React**: `App.tsx`, `router.ts`, `routes.ts` for route configs
- **Express**: `app.get('/...')`, `router.post('/...')` patterns
- **FastAPI**: `@app.get('/...')` decorators
- **Layout/Nav files**: `<Link href="...">` and `<a href="...">` tags

### Example:
~~~yaml
build_navigation_map:
  project_dir: /path/to/your-project
  framework: nextjs
~~~

### Output:
- **`docs/navigation-map.md`** — Human-readable map for review
- **`docs/navigation-map.json`** — Machine-readable map for E2E agent

Contains:
- Every frontend route with its source file
- Every API endpoint with HTTP methods and source file
- All navigation links found in layout/component files
- Framework detection results
- **Orphan Links**: Links found in components (nav, sidebar, CTAs, or any
  other interactive element) that reference paths with no corresponding route
  file. These indicate interactive elements that will be dead on delivery and
  must be addressed before E2E verification.

### Comparison Workflow:
1. Run `build_navigation_map` → get the "as-designed" route list
2. Start dev server
3. `curl` every route from the map → get the "as-built" status
4. Diff: routes in map but returning 404 = **missing implementation**
5. Diff: routes responding 200 but NOT in map = **undocumented route**
