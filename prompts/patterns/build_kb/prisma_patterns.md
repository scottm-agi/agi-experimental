# Prisma ORM Patterns — Build KB

Production-ready patterns for Prisma ORM with Next.js. Covers singleton setup, Prisma 7 migration, connection pooling, and Docker/CI configuration.

## Singleton Pattern (Hot Reload Safe)

In development, Next.js HMR creates new `PrismaClient` instances on every reload, exhausting database connections.

```typescript
// lib/prisma.ts — The canonical singleton pattern
import { PrismaClient } from '@prisma/client'  // Prisma 6
// import { PrismaClient } from './generated/prisma/client'  // Prisma 7

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

export const prisma = globalForPrisma.prisma ?? new PrismaClient({
  log: process.env.NODE_ENV === 'development' ? ['query', 'warn', 'error'] : ['error'],
})

if (process.env.NODE_ENV !== 'production') {
  globalForPrisma.prisma = prisma
}

export default prisma
```

**Why this matters:** Without the singleton, each HMR cycle creates a new PrismaClient. After ~100 reloads, PostgreSQL hits its connection limit (default 100) and the app crashes with `too many clients already`.

## Prisma 7 Migration Guide (2026)

Prisma 7 introduced **breaking changes**. Projects using Prisma 5/6 patterns WILL fail.

### Key Breaking Changes

1. **Driver Adapters Required**: PrismaClient no longer connects to databases directly. You MUST use a driver adapter.

```typescript
// Prisma 7 — REQUIRED adapter pattern
import { PrismaClient } from './generated/prisma/client'
import { PrismaPg } from '@prisma/adapter-pg'

const adapter = new PrismaPg({ connectionString: process.env.DATABASE_URL! })
const prisma = new PrismaClient({ adapter })
```

2. **Output Field Required**: The `output` field is now mandatory in the generator block. Prisma Client is no longer generated in `node_modules`.

```prisma
// schema.prisma — Prisma 7
generator client {
  provider = "prisma-client"    // NOT "prisma-client-js"
  output   = "../src/generated/prisma"
}
```

3. **Provider Rename**: `prisma-client-js` → `prisma-client`. The old provider will be removed in future releases.

4. **URL Removed from Schema**: The `url` property is no longer supported in schema datasource blocks. Connection URLs go in `prisma.config.ts`.

```typescript
// prisma.config.ts — Prisma 7
import path from 'node:path'
import { defineConfig } from 'prisma/config'

export default defineConfig({
  earlyAccess: true,
  schema: path.join('prisma', 'schema.prisma'),
})
```

5. **Middleware Removed**: `prisma.$use()` is gone. Use Prisma Client Extensions instead.

```typescript
// Before (Prisma 6) — BROKEN in v7
prisma.$use(async (params, next) => { ... })

// After (Prisma 7)
const xprisma = prisma.$extends({
  query: {
    $allOperations({ args, query }) {
      // your middleware logic
      return query(args)
    }
  }
})
```

### Prisma 7 Singleton (Full Pattern)

```typescript
// lib/prisma.ts — Prisma 7 singleton with adapter
import { PrismaClient } from '../generated/prisma/client'
import { PrismaPg } from '@prisma/adapter-pg'

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

function createPrismaClient() {
  const adapter = new PrismaPg({
    connectionString: process.env.DATABASE_URL!,
  })
  return new PrismaClient({ adapter })
}

export const prisma = globalForPrisma.prisma ?? createPrismaClient()

if (process.env.NODE_ENV !== 'production') {
  globalForPrisma.prisma = prisma
}
```

## Connection Pooling

### Prisma 6 vs 7 Differences
- **Prisma 6**: Built-in connection pool with 5s timeout by default.
- **Prisma 7**: Uses the underlying driver's pool settings. The `pg` driver has **no timeout by default** (`0`).

### Pooling Configuration (Prisma 7 + pg)
```typescript
import { Pool } from 'pg'
import { PrismaPg } from '@prisma/adapter-pg'

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 10,                    // Max connections
  idleTimeoutMillis: 30000,   // Close idle connections after 30s
  connectionTimeoutMillis: 5000, // Fail fast on connection
})

const adapter = new PrismaPg({ pool })
const prisma = new PrismaClient({ adapter })
```

### Serverless/Edge Considerations
- Use Prisma Accelerate or PgBouncer for connection pooling in serverless environments.
- Each serverless invocation creates a new connection without pooling.
- Set `max: 1` in serverless to prevent connection exhaustion.

## Docker / CI Build Configuration

Prisma engine binaries must match the runtime platform. Docker builds on macOS need explicit binary targets.

```prisma
generator client {
  provider      = "prisma-client"
  output        = "../src/generated/prisma"
  binaryTargets = ["native", "linux-musl-openssl-3.0.x"]
}
```

### Build Script Pattern
```bash
# Dockerfile or CI script
npx prisma generate          # Generate client for current platform
npx prisma db push           # Dev: push schema changes
# OR
npx prisma migrate deploy    # Prod: apply pending migrations
```

## Common Error Signatures

| Error | Root Cause | Fix |
|-------|-----------|-----|
| `PrismaClient is not a constructor` | Client not generated | `npx prisma generate` |
| `PrismaClientConstructorValidationError` | Invalid constructor options or build-time execution | Use singleton pattern |
| `PrismaClientInitializationError` | Binary not found / platform mismatch | Set `binaryTargets` in schema |
| `datasource property url is no longer supported` | Prisma 7 breaking change | Move URL to `prisma.config.ts`, use adapter |
| `prisma-client-js provider will be removed` | Prisma 7 provider rename | Change to `prisma-client` |
| `$use is no longer supported` | Prisma 7 middleware removal | Use `$extends` instead |
