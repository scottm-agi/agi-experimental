# 🔴 React + TypeScript Universal Gotcha Cheatsheet

> Injected proactively into frontend/architect agents. Source: typescript-cheatsheets/react (47K⭐) + awesome-cursorrules (39K⭐).

## 1. HTML Attribute Conflicts — Use `Omit<>`
When extending native HTML elements, custom props may clash with intrinsic attributes (e.g., `size` is `number` on `<button>`).
```tsx
// ❌ WRONG — 'size' conflicts with HTMLButtonElement.size
interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  size?: 'sm' | 'md' | 'lg'; // TSC error: type conflict
}

// ✅ CORRECT — Omit the conflicting native attribute
interface ButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'size'> {
  size?: 'sm' | 'md' | 'lg';
}
```

## 2. `'use client'` Directive — FIRST LINE
Any file using hooks (`useState`, `useEffect`, etc.), event handlers, or browser APIs MUST have `'use client';` as line 1.

## 3. Null Safety — Always Guard
`Object is possibly 'undefined'` → use optional chaining (`?.`) or null checks before access.
```tsx
const [user, setUser] = useState<User | null>(null);
// ✅ user?.name   ❌ user.name
```

## 4. Event Handler Typing
```tsx
// ✅ onChange handler
const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => { ... }
// ✅ onClick handler
const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => { ... }
// ✅ onSubmit handler
const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => { e.preventDefault(); ... }
```

## 5. useState with Complex Types
Always provide a generic type for non-primitive state:
```tsx
const [items, setItems] = useState<Item[]>([]);
const [user, setUser] = useState<User | null>(null);
```

## 6. App Router — NEVER import `next/document`
Use `layout.tsx` with `<html>` and `<body>` directly. Never use `<Html>`, `<Head>`, `<Main>`, `<NextScript>`.

## 7. Dynamic Imports for Client-Only Components
```tsx
const Map = dynamic(() => import('./Map'), { ssr: false });
```

## 8. forwardRef Typing (React <19)
```tsx
const Input = forwardRef<HTMLInputElement, InputProps>((props, ref) => (
  <input ref={ref} {...props} />
));
```

## 9. Union Types over Enums
Prefer `type Status = 'active' | 'inactive'` over `enum Status { ... }`.

## 10. Shared Type Contracts
ALL types used across 2+ files MUST live in `src/types/index.ts`. Never redefine types locally.
