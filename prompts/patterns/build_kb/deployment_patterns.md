# Deployment Patterns — Build KB

Production deployment patterns for Next.js applications on Railway, Vercel, and Docker.

## Environment Variable Management

### Next.js Environment Variable Rules
- **Server-only**: `DATABASE_URL`, `API_SECRET` — accessible only in Server Components, Route Handlers, and `getServerSideProps`
- **Client-exposed**: Must use `NEXT_PUBLIC_` prefix — `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_STRIPE_KEY`
- **Build-time**: Variables are embedded at `npm run build` time, NOT at runtime for client code

```bash
# .env.local (development)
DATABASE_URL=postgresql://localhost:5432/mydb
NEXT_PUBLIC_API_URL=http://localhost:3000/api

# .env.production (production build)
DATABASE_URL=postgresql://prod-host:5432/mydb
NEXT_PUBLIC_API_URL=https://api.example.com
```

### Common Mistakes
1. **Using `process.env` in client code without `NEXT_PUBLIC_` prefix** → Variable is `undefined`
2. **Changing env vars after build without rebuilding** → Client code still uses old values
3. **Hardcoding secrets in code** → Security vulnerability

## Railway Deployment

### Recommended Configuration
```bash
# Build command
npm run build

# Start command (standalone mode)
node .next/standalone/server.js

# Or with Prisma
npx prisma generate && npx prisma migrate deploy && npm run build
```

### Railway-Specific Patterns
- Set `PORT` env var — Railway provides this automatically
- Use `RAILWAY_ENVIRONMENT` to detect environment
- Health checks: Railway pings `GET /` by default

### Nixpacks Build (Default)
Railway uses Nixpacks to auto-detect and build. For Next.js:
```json
// package.json
{
  "scripts": {
    "build": "next build",
    "start": "next start -p ${PORT:-3000}"
  }
}
```

## Docker Deployment

### Next.js Standalone Dockerfile
```dockerfile
FROM node:20-alpine AS base

# Dependencies
FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --only=production

# Build
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npx prisma generate  # If using Prisma
RUN npm run build

# Production
FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000
CMD ["node", "server.js"]
```

### Key Configuration
```javascript
// next.config.ts
const nextConfig = {
  output: 'standalone',       // Required for Docker
  experimental: {
    outputFileTracingRoot: '.', // For monorepos
  },
}
```

## Health Check Endpoints

### Pattern for Zero-Downtime Deploys
```typescript
// app/api/health/route.ts
import { NextResponse } from 'next/server'

export async function GET() {
  try {
    // Check database connectivity
    await prisma.$queryRaw`SELECT 1`
    return NextResponse.json({ status: 'healthy', timestamp: new Date().toISOString() })
  } catch (error) {
    return NextResponse.json(
      { status: 'unhealthy', error: (error as Error).message },
      { status: 503 }
    )
  }
}
```

## Vercel Deployment

### Key Differences from Self-Hosted
- Automatic `NEXT_PUBLIC_` variable injection at build time
- Serverless functions have 10s default timeout (Pro: 60s)
- Edge Runtime available for middleware and specific routes
- No persistent filesystem — use external storage (S3, Vercel Blob)

### Common Vercel Errors
| Error | Fix |
|-------|-----|
| `FUNCTION_INVOCATION_TIMEOUT` | Increase timeout in `vercel.json` or optimize the function |
| `EDGE_FUNCTION_INVOCATION_FAILED` | Edge-incompatible APIs used (Node.js-only modules) |
| Build cache stale | Clear build cache in project settings |

## Deployment Checklist

1. ✅ All env vars set in deployment platform
2. ✅ `NEXT_PUBLIC_` prefix for client-side vars
3. ✅ Database migrations applied (`prisma migrate deploy`)
4. ✅ Health check endpoint responding
5. ✅ `output: 'standalone'` set for Docker deployments
6. ✅ `PORT` env var configured
7. ✅ No hardcoded localhost URLs in production code
