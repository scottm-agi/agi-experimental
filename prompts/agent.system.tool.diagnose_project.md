# diagnose_project

Diagnose and auto-fix project build, configuration, and infrastructure issues.

## When to Use
- **ALWAYS** before declaring a task complete to verify project health
- When a build fails or the app renders without styles
- When the completion gate blocks with Tailwind/PostCSS/build errors
- After clearing build caches or modifying framework configs
- When a dev server starts but the page looks broken

## Actions

### diagnose
Run all health checks and get a structured report:
```json
{
    "action": "diagnose",
    "path": "/path/to/project"
}
```

Returns:
- ❌ Critical failures that MUST be fixed (empty Tailwind content, corrupted cache)
- ⚠️ Warnings that should be addressed (missing PostCSS config)
- ✅ Passed checks
- 🔧 Auto-fixable issues

### fix
Auto-remediate all fixable issues:
```json
{
    "action": "fix",
    "path": "/path/to/project"
}
```

Auto-fixes include:
- Patches empty Tailwind `content: []` with inferred source file paths
- Creates missing `postcss.config.js` with tailwindcss/autoprefixer plugins
- Clears corrupted build caches (`.next`, `dist`, `build`)

## Checks Performed

| Check | Framework | Severity | Auto-Fix |
|-------|-----------|----------|----------|
| Tailwind config exists | Tailwind | Critical | `npx tailwindcss init` |
| Tailwind content paths populated | Tailwind | Critical | Patches config with inferred paths |
| PostCSS config with tailwindcss plugin | PostCSS | Warning | Creates postcss.config.js |
| Build cache integrity | Next.js/Vite/CRA | Critical | Clears corrupted cache dir |
| NPM dependencies installed | Node.js | Critical | `npm install` |
| Build output exists | Node.js | Info | `npm run build` |

## Workflow
1. Run `diagnose` to get the full report
2. If auto-fixable issues found, run `fix`
3. Re-run `diagnose` to verify fixes
4. Continue with your task

## Important
- Always run **diagnose before fix** to understand what will change
- After `fix` clears a build cache, you must run `npm run build` or restart the dev server
- The tool does NOT start dev servers or run builds — it only fixes configs and clears caches
