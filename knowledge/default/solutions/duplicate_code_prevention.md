# Duplicate Code Prevention

## Problem
Agents copy-paste similar logic across multiple files instead of extracting shared functionality into a single reusable module. This creates maintenance burden and inconsistent behavior.

## Wrong (common mistake)
```javascript
// file: api/prospects.ts
function calculateScore(prospect) {
  return prospect.reviews * 0.4 + prospect.rating * 0.6;
}

// file: api/outreach.ts  (DUPLICATE!)
function calculateScore(prospect) {
  return prospect.reviews * 0.4 + prospect.rating * 0.6;
}
```

## Correct Pattern
```javascript
// file: lib/scoring.ts  (SHARED MODULE)
export function calculateScore(prospect) {
  return prospect.reviews * 0.4 + prospect.rating * 0.6;
}

// file: api/prospects.ts
import { calculateScore } from "@/lib/scoring";

// file: api/outreach.ts
import { calculateScore } from "@/lib/scoring";
```

## Rules
1. Before writing a function, search the project for similar existing implementations
2. If a function already exists elsewhere, import it — do NOT copy-paste
3. Extract any logic used in 2+ files into a shared module under `lib/`, `utils/`, or `helpers/`
4. Each file should follow single-responsibility: one concern, universally importable
5. Use function parameters to handle per-caller differences instead of duplicating with slight variations

## When This Appears
- Building multiple API routes that share validation, scoring, or transformation logic
- Creating frontend components that use the same data formatting
- Implementing multiple endpoints that query similar data patterns
- Any time you think "this is similar to what I wrote in the other file"
