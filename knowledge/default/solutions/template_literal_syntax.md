# TypeScript/JavaScript Template Literal Syntax

## Problem
`${}` string interpolation ONLY works inside backtick template literals (`` ` ``), NOT inside regular quotes (`"` or `'`).

## Wrong (common mistake)
```javascript
// Double quotes — interpolation does NOT work
const url = "https://api.example.com/${userId}/profile"
// Output: "https://api.example.com/${userId}/profile" (literal text)

const greeting = "Hello, ${name}!"
// Output: "Hello, ${name}!" (literal text)
```

## Correct
```javascript
// Backticks — interpolation WORKS
const url = `https://api.example.com/${userId}/profile`
// Output: "https://api.example.com/123/profile"

const greeting = `Hello, ${name}!`
// Output: "Hello, John!"
```

## When This Appears
- Building dynamic URLs with path parameters
- Constructing HTML strings with embedded variables
- Creating dynamic class names or style strings
- Any string that needs variable interpolation in JS/TS

## Recovery Pattern
If a build or test fails with unexpected literal `${...}` in output:
1. Search for `"${` (dollar-brace inside double quotes) — these are bugs
2. Replace the surrounding quotes with backticks
3. Verify the interpolated values are in scope at that point in the code
