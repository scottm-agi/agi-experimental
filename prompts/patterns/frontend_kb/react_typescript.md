# React + TypeScript Patterns

> Deep-dive reference for React component typing with TypeScript.
> Source: typescript-cheatsheets/react (47K⭐)

## Extending HTML Element Props

When wrapping native HTML elements, use `Omit<>` to avoid prop conflicts:

```tsx
// ✅ Safe pattern — omit conflicting native props
interface ButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'size' | 'color'> {
  size?: 'sm' | 'md' | 'lg';
  color?: 'primary' | 'secondary';
}

// ✅ Alternative — use ComponentPropsWithoutRef
type InputProps = Omit<React.ComponentPropsWithoutRef<'input'>, 'size'> & {
  size?: 'small' | 'large';
};
```

**Common conflicting props**: `size` (number on button/input), `color` (string on many elements), `width`/`height` (number on img/video), `form` (string on many elements).

## useState Typing

```tsx
// Simple — inference works
const [count, setCount] = useState(0);

// Complex — use generic
const [user, setUser] = useState<User | null>(null);
const [items, setItems] = useState<Item[]>([]);

// ❌ Never use {} as User — leads to runtime errors
const [user, setUser] = useState<User>({} as User); // dangerous
```

## useRef Typing

```tsx
// DOM element ref — pass null, get read-only ref
const divRef = useRef<HTMLDivElement>(null);

// Mutable value ref — include null in the union
const intervalRef = useRef<number | null>(null);
```

## Event Handler Typing

| Event | Type |
|-------|------|
| onChange (input) | `React.ChangeEvent<HTMLInputElement>` |
| onChange (select) | `React.ChangeEvent<HTMLSelectElement>` |
| onClick (button) | `React.MouseEvent<HTMLButtonElement>` |
| onSubmit (form) | `React.FormEvent<HTMLFormElement>` |
| onKeyDown | `React.KeyboardEvent<HTMLElement>` |

```tsx
// ✅ Inline — type is inferred
<button onClick={(e) => { /* e is React.MouseEvent */ }} />

// ✅ Separate handler
const handleChange: React.ChangeEventHandler<HTMLInputElement> = (e) => {
  setValue(e.target.value);
};
```

## forwardRef Typing

```tsx
// React 19+ — ref is a regular prop
function MyInput(props: React.ComponentPropsWithRef<'input'>) {
  return <input {...props} />;
}

// React 18 — use forwardRef wrapper
const MyInput = forwardRef<HTMLInputElement, InputProps>((props, ref) => (
  <input ref={ref} {...props} />
));
```

## Custom Hook Return Types

```tsx
// ✅ Use `as const` for tuple returns
function useToggle(initial: boolean) {
  const [value, setValue] = useState(initial);
  const toggle = useCallback(() => setValue(v => !v), []);
  return [value, toggle] as const; // [boolean, () => void]
}
```

## useReducer with Discriminated Unions

```tsx
type Action =
  | { type: 'increment'; payload: number }
  | { type: 'reset' };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'increment': return { count: state.count + action.payload };
    case 'reset': return { count: 0 };
  }
}
```

## Context Typing

```tsx
// ✅ Create with explicit type and null default
const ThemeContext = createContext<Theme | null>(null);

// ✅ Custom hook with null check
function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
```

## Union Types over Enums

```tsx
// ✅ Prefer union types — tree-shakeable, no runtime overhead
type Status = 'active' | 'inactive' | 'pending';

// ❌ Avoid enums — add runtime code, can't be tree-shaken
enum Status { Active = 'active', Inactive = 'inactive' }
```

## Utility Types Quick Reference

| Utility | Use Case |
|---------|----------|
| `Partial<T>` | Make all props optional |
| `Required<T>` | Make all props required |
| `Pick<T, K>` | Select specific props |
| `Omit<T, K>` | Remove specific props |
| `Record<K, V>` | Object with typed keys/values |
| `ReturnType<T>` | Extract function return type |
| `ComponentProps<C>` | Extract component props |
