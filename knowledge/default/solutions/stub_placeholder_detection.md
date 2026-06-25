# Stub and Placeholder Detection

## Problem
Agents write stub implementations with comments like "In a real app" or "TODO: implement" that pass basic build checks but deliver zero business value. These stubs silently survive into production.

## Stub Indicators (Red Flags)
```javascript
// "In a real app, this would..."
// "TODO: implement actual logic"
// "Placeholder for future implementation"
return [] // empty array instead of real data
return {} // empty object instead of real data
return null // null instead of real computation
throw new Error("Not implemented")
console.log("TODO")
```

## Rules
1. NEVER write stub implementations — implement the real logic or explicitly fail with a descriptive error
2. If an external API/service is needed and not yet configured, implement the FULL integration with environment variable configuration, not a stub
3. Every function that returns data must return REAL computed/fetched data, not hardcoded empty arrays or placeholder objects
4. Before completing any task, search the codebase for these patterns:
   - `// In a real`
   - `// TODO`
   - `// Placeholder`
   - `return []` or `return {}` (in data-fetching functions)
5. Replace every stub with either real implementation OR a clear error that prevents silent failure

## When This Appears
- Building API endpoints that should fetch from databases or external services
- Implementing data processing pipelines
- Creating integration layers between frontend and backend
- Any code that is "good enough to compile" but not "good enough to ship"
