---
name: api-backend
description: >
  Headless API and backend service development orchestration. Covers API design,
  contract-first development, database schemas, authentication, endpoint
  implementation, stub detection, and integration testing. Activates for
  API-only, backend-only, microservice, or headless server projects.
triggers:
  - REST API
  - GraphQL
  - API endpoint
  - endpoints
  - microservice
  - API only
  - backend only
  - headless
  - server-side
  - API server
  - backend service
  - API design
trigger_patterns:
  - 'build.*api'
  - 'create.*endpoint'
  - '(rest|graphql|grpc).*api'
  - 'api.*server'
  - 'backend.*service'
  - 'microservice.*architect'
  - 'headless.*backend'
anti_triggers:
  - landing page
  - UI design
  - CSS styling
  - React component
  - Vue component
  - Svelte component
  - frontend styling
  - web app
  - website
  - dashboard
  - user interface
  - responsive layout
  - animations
skill_type: task_specific
---

# API Backend Development Orchestration Skill

This skill extends the core orchestrator with API-specific phases, contract-first
mandates, stub detection gates, and verification patterns for building headless
backend services.

> **When to activate**: API-only projects with NO frontend component. If the request
> involves both frontend AND backend, use the `fullstack-dev` skill instead.

---

## Phase 1: API Contract Design

**Delegate to: `architect`**

Before ANY implementation begins, the API contract MUST be defined:

### Contract-First Development (RCA-1777 Mandate)

> **Root Cause**: Parallel frontend/backend builds without shared contracts produced
> non-functional backends with stub responses that returned `{ success: true }`.

1. **Define the API Schema** — OpenAPI spec, GraphQL schema, or TypeScript interfaces
2. **Enumerate ALL endpoints** with:
   - HTTP method + path (REST) or query/mutation (GraphQL)
   - Request body / parameters schema
   - Response body schema with REAL field names
   - Error response codes and bodies
   - Authentication requirements per endpoint
3. **Write the contract file** — Save as `api-contract.yaml` or `schema.graphql` at project root
4. **Share contract with ALL agents** — This file is the single source of truth

### Database Schema Design

1. Define data models (entities, relationships, constraints)
2. Specify indexes and unique constraints
3. Plan migration strategy (Prisma, Drizzle, SQLAlchemy, Knex)
4. Document seed data requirements

**Gate**: Phase 1 is NOT complete until the contract file exists and covers ALL requested endpoints.

---

## Phase 2: Task Decomposition

**Orchestrator Mandate (RCA-210 S7)**

> **Root Cause**: Without mandatory decomposition, agents completed only the UI layer
> and reported "done" with stubs for backend logic.

### Mandatory Decomposition Rules

For EVERY feature in the user's request:

1. **Identify the data model** — What entities does this feature touch?
2. **Identify the API endpoints** — What CRUD operations are needed?
3. **Identify the business logic** — What validation, transformation, or computation?
4. **Identify the integration** — What external services or APIs?
5. **Create separate delegations** for each:

```
Feature: "User Authentication"
├── Delegation 1: Database schema (users table, sessions table)
├── Delegation 2: Auth endpoints (POST /auth/login, POST /auth/register, POST /auth/logout)
├── Delegation 3: Middleware (JWT validation, session management)
└── Delegation 4: Integration tests (curl-based E2E)
```

**Gate**: The orchestrator MUST NOT delegate a feature as a single blob.
Each delegation must target ONE specific layer.

---

## Phase 3: Implementation

**Delegate to: `code`**

### 🔴 Port Management — `services_mgt` MANDATORY (ITR-20 PS-001)

> **Root Cause**: Hardcoded `localhost:3000` caused port conflicts in multi-service
> environments and broke E2E testing when ports were already in use.

**HARD RULE**: NEVER hardcode port numbers. ALL server startup MUST use `services_mgt`:
```
services_mgt start_service --name "api-server" --command "npm run dev" --port_preference 3000
```
The `services_mgt` tool handles port allocation, conflict detection, and lifecycle management.
All subsequent references to the server MUST use the port returned by `services_mgt list_services`.

### Anti-Stub Gate (Quality Audit Mandate)

> **Root Cause**: ALL API routes were 5-line stubs returning `{ success: true }`.
> HTTP 200 checks passed, masking the complete absence of business logic.

**HARD RULES — Stub Detection**:

1. **NO response-only handlers** — Every endpoint MUST:
   - Read from or write to a database/store
   - Perform input validation
   - Handle at least 2 error cases (400, 404, 500)
   - Return real, schema-compliant response bodies

2. **Banned patterns** (auto-fail if detected):
   ```
   ❌ res.json({ success: true })
   ❌ res.json({ message: "OK" })
   ❌ return { status: "ok" }
   ❌ // TODO: implement
   ❌ const mockData = [...]
   ❌ const sampleData = [...]
   ```

3. **Required patterns** (must exist in every handler):
   ```
   ✅ try { ... } catch (error) { ... }
   ✅ if (!req.body.field) return res.status(400)
   ✅ await db.query(...) OR await model.findMany(...)
   ✅ return res.status(201).json({ id: result.id, ... })
   ```

### Manifest → Endpoint Binding Contract (ITR-20 PS-004)

Every API endpoint in the manifest MUST map to:
1. A route file with handler implementation
2. A database model or data access function
3. An entry in the API contract (OpenAPI/schema)

If the manifest says "user authentication" → there MUST be `/auth/login`, `/auth/register`, etc.

### Anti-Mock-Data Linter (Quality Audit RC-B)

> **Root Cause**: `const mockData = [...]` arrays persisted in production builds
> because agents used them during development and never replaced them.

**HARD RULE**: Mock data declarations are FORBIDDEN in implementation code.
All data MUST come from database queries, external API calls, or request parameters.

### Post-Scaffold Inventory (ITR-20 PS-005)

After scaffolding, run:
```bash
cat package.json  # Check name ≠ template default
ls src/            # Verify real route files exist
grep -r 'TODO\|FIXME\|placeholder' src/
```
Scaffold boilerplate MUST be replaced before proceeding to verification.

### Cross-Wave Context Injection (Quality Audit RC-C)

> **Root Cause**: Service libraries were created in Wave 1 but orphaned in Wave 2
> because different delegation waves had no shared context.

**Mandate**: Every delegation MUST include:
1. A file inventory of what was created in previous waves
2. The API contract file path
3. Explicit instruction to import and USE existing service libraries

---

## Phase 4: Verification

**Delegate to: `code`**

### Endpoint Verification Checklist

For EVERY endpoint defined in the API contract:

1. **Route exists** — The endpoint is registered and accessible
2. **Method matches** — GET/POST/PUT/DELETE matches the contract
3. **Request validation** — Invalid inputs return 400 with descriptive errors
4. **Success response** — Valid inputs return correct status code and schema-compliant body
5. **Error handling** — Missing resources return 404, server errors return 500
6. **Auth enforcement** — Protected endpoints reject unauthenticated requests

### Pre-E2E Smoke Check (ITR-20 PS-006)

Before running E2E tests, verify the server is actually responsive:
```bash
# Get port from services_mgt, then smoke check
PORT=$(services_mgt list_services | grep api-server | awk '{print $3}')
curl -sf http://localhost:$PORT/health || echo "FAIL: Server not responding"
```
Do NOT run the full E2E suite against a broken or unresponsive server.

### E2E Test Generation

Generate curl-based tests for ALL endpoints. **Use dynamic port from `services_mgt`**:

```bash
# Get the allocated port (NEVER hardcode 3000)
PORT=$(services_mgt list_services | grep api-server | awk '{print $3}')

# Test: POST /api/users — Create user
curl -X POST http://localhost:$PORT/api/users \
  -H "Content-Type: application/json" \
  -d '{"name": "Test User", "email": "test@example.com"}' \
  -w "\n%{http_code}" | tail -1 | grep -q "201"

# Test: GET /api/users/:id — Get user by ID  
curl -s http://localhost:$PORT/api/users/1 \
  -w "\n%{http_code}" | tail -1 | grep -q "200"

# Test: POST /api/users — Validation error
curl -X POST http://localhost:$PORT/api/users \
  -H "Content-Type: application/json" \
  -d '{}' \
  -w "\n%{http_code}" | tail -1 | grep -q "400"
```

### Database Verification

1. Tables exist with correct schema
2. Seed data loaded (if specified)
3. Foreign key constraints enforced
4. Indexes applied

---

## Phase 5: Build & Health Check

1. **Build passes** — `npm run build` or equivalent exits 0
2. **Server starts** — Process binds to PORT and responds to health check
3. **No mock data** — `grep -r "mockData\|sampleData\|const mock" src/` returns empty
4. **No stubs** — `grep -r "TODO.*implement\|success.*true" src/routes/` returns empty
5. **All E2E tests pass** — curl test script exits 0

---

## Tech Stack Defaults (Override with user preference)

| Layer | Default | Alternatives |
|-------|---------|-------------|
| Runtime | Node.js + Express | Fastify, Hono, Python/FastAPI |
| Database | PostgreSQL + Prisma | SQLite, MongoDB, Drizzle |
| Auth | JWT + bcrypt | Clerk, Auth0, Passport |
| Validation | Zod | Joi, Yup, class-validator |
| Testing | Vitest + supertest | Jest, Mocha |
| API Docs | OpenAPI 3.0 | GraphQL introspection |

### 🔴 Database Provider Compatibility (ITR-20 PS-007)

Before selecting a database provider, research its limitations:
- **SQLite**: No native enums, no `ALTER COLUMN`, limited concurrent writes, no JSON operators
- **MongoDB**: No JOINs, no transactions (without replica set), different query patterns
- **PostgreSQL**: Full-featured but requires external service provisioning

If the user specifies features that need enums, complex queries, or transactions,
do NOT default to SQLite — use PostgreSQL or explicitly warn about limitations.
