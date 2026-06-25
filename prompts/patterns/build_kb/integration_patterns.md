# Integration Patterns — Build KB

API routes, Server Actions, database connections, and authentication patterns for Next.js 15.

## Route Handlers (API Routes)

### App Router Route Handlers
```typescript
// app/api/users/route.ts
import { NextRequest, NextResponse } from 'next/server'
import { prisma } from '@/lib/prisma'

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams
  const page = parseInt(searchParams.get('page') || '1')

  const users = await prisma.user.findMany({
    skip: (page - 1) * 10,
    take: 10,
  })

  return NextResponse.json(users)
}

export async function POST(request: NextRequest) {
  const body = await request.json()

  const user = await prisma.user.create({
    data: body,
  })

  return NextResponse.json(user, { status: 201 })
}
```

### Dynamic Route Handlers
```typescript
// app/api/users/[id]/route.ts
import { NextRequest, NextResponse } from 'next/server'

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params  // Next.js 15: params is a Promise
  const user = await prisma.user.findUnique({ where: { id } })

  if (!user) {
    return NextResponse.json({ error: 'Not found' }, { status: 404 })
  }

  return NextResponse.json(user)
}
```

**Next.js 15 Breaking Change:** `params` is now a `Promise` in route handlers and page components. You MUST `await` it.

## Server Actions (Next.js 15)

### Basic Server Action
```typescript
// app/actions/users.ts
'use server'

import { revalidatePath } from 'next/cache'
import { prisma } from '@/lib/prisma'

export async function createUser(formData: FormData) {
  const name = formData.get('name') as string
  const email = formData.get('email') as string

  await prisma.user.create({
    data: { name, email },
  })

  revalidatePath('/users')  // Revalidate the users list page
}
```

### Server Action with Validation
```typescript
'use server'

import { z } from 'zod'

const CreateUserSchema = z.object({
  name: z.string().min(1, 'Name is required'),
  email: z.string().email('Invalid email'),
})

export async function createUser(formData: FormData) {
  const parsed = CreateUserSchema.safeParse({
    name: formData.get('name'),
    email: formData.get('email'),
  })

  if (!parsed.success) {
    return { error: parsed.error.flatten().fieldErrors }
  }

  await prisma.user.create({ data: parsed.data })
  revalidatePath('/users')
  return { success: true }
}
```

### Using Server Actions in Client Components
```typescript
'use client'

import { createUser } from '@/app/actions/users'
import { useActionState } from 'react'  // React 19

export function CreateUserForm() {
  const [state, formAction, isPending] = useActionState(createUser, null)

  return (
    <form action={formAction}>
      <input name="name" required />
      <input name="email" type="email" required />
      <button type="submit" disabled={isPending}>
        {isPending ? 'Creating...' : 'Create User'}
      </button>
      {state?.error && <p>{JSON.stringify(state.error)}</p>}
    </form>
  )
}
```

### Server Action Rules
1. **Always** add `'use server'` directive at the top of the file or function
2. Server Actions can ONLY be called from Client Components or `<form action={...}>`
3. Use `revalidatePath()` or `revalidateTag()` after mutations
4. Server Actions run on the server — safe to use database, secrets, etc.
5. Return serializable data only (no class instances, functions, or DOM nodes)

## Database Connection Patterns

### Serverless Connection Pooling
In serverless environments (Vercel, Lambda), each invocation creates a new connection. Use pooling:

```typescript
// Option 1: PgBouncer (external pooler)
// DATABASE_URL=postgresql://user:pass@pgbouncer-host:6432/mydb?pgbouncer=true

// Option 2: Prisma Accelerate (managed pooler)
// Use accelerateUrl in PrismaClient constructor

// Option 3: Connection limit per instance
const prisma = new PrismaClient({
  datasources: {
    db: { url: process.env.DATABASE_URL },
  },
})
```

### Transaction Patterns
```typescript
// Interactive transaction
const result = await prisma.$transaction(async (tx) => {
  const user = await tx.user.create({ data: { name: 'Alice' } })
  const order = await tx.order.create({
    data: { userId: user.id, total: 100 },
  })
  return { user, order }
})
```

## Authentication Patterns

### Middleware-Based Auth (Next.js 15)
```typescript
// middleware.ts
import { NextRequest, NextResponse } from 'next/server'

export function middleware(request: NextRequest) {
  const token = request.cookies.get('session')?.value

  if (!token && request.nextUrl.pathname.startsWith('/dashboard')) {
    return NextResponse.redirect(new URL('/login', request.url))
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/dashboard/:path*', '/api/protected/:path*'],
}
```

### Server Component Auth Check
```typescript
// app/dashboard/page.tsx
import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { verifySession } from '@/lib/auth'

export default async function DashboardPage() {
  const cookieStore = await cookies()  // Next.js 15: cookies() is async
  const session = cookieStore.get('session')

  if (!session) {
    redirect('/login')
  }

  const user = await verifySession(session.value)
  return <Dashboard user={user} />
}
```

**Next.js 15 Breaking Change:** `cookies()`, `headers()`, and `draftMode()` are now async. You MUST `await` them.

## Error Handling Patterns

### Global Error Boundary
```typescript
// app/error.tsx
'use client'

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <div>
      <h2>Something went wrong!</h2>
      <button onClick={reset}>Try again</button>
    </div>
  )
}
```

### API Error Response Pattern
```typescript
// Standard error response shape
type ApiError = {
  error: string
  code: string
  details?: Record<string, string[]>
}

function errorResponse(message: string, code: string, status: number) {
  return NextResponse.json({ error: message, code } satisfies ApiError, { status })
}
```
