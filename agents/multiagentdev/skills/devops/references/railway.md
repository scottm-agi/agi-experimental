# Railway Deployment Reference

## Overview

This reference covers deploying applications to Railway, managing services and
environments, configuring environment variables, and verifying deployments using
the Railway CLI.

---

## Prerequisites

```bash
# Verify Railway CLI is installed
railway --version

# If not installed (macOS):
brew install railway

# Login
railway login

# Link to project
railway link
```

---

## Service Architecture

### AGIX Service Map

| Service | Deploy Directory | Start Command | Build System |
|---------|-----------------|---------------|-------------|
| **Frontend** | `frontend/` | `npx serve dist -p $PORT` | Dockerfile (nginx) |
| **Backend** | `frontend/server/` | `node dist/index.js` | Nixpacks |
| **Signal API** | repo root | `node server/start.js` | Nixpacks |

> ⚠️ **CRITICAL**: Always verify you're deploying from the correct directory.
> Deploying backend from `frontend/` will use the nginx Dockerfile — WRONG.

---

## Deploy

### Standard Deploy (Detached)
```bash
# Deploy current directory
railway up --detach

# Deploy specific service
railway up --detach -s <service-name>

# Deploy from specific directory
cd /path/to/service && railway up --detach
```

### Deploy Verification (MANDATORY)

**NEVER fire-and-forget.** Always verify the deploy landed:

```bash
# 1. Check deploy status
railway status

# 2. Check logs for startup
railway logs --tail 50

# 3. Poll health endpoint until responsive
until curl -sf https://<domain>.up.railway.app/health; do
  echo "Waiting for health check..."
  sleep 5
done

# 4. Verify deployed commit SHA matches expected
# (Use Railway API or dashboard to confirm)
```

---

## Environment Variables

### Set Variables
```bash
# Set a single variable
railway variables set KEY=value

# Set multiple variables
railway variables set \
  DATABASE_URL="postgresql://..." \
  JWT_SECRET="..." \
  NODE_ENV="production"
```

### View Variables
```bash
# List all variables
railway variables

# Get specific variable
railway variables get DATABASE_URL
```

### Environment-Specific Config

| Variable | Staging | Production |
|----------|---------|------------|
| `NODE_ENV` | `staging` | `production` |
| `LOG_LEVEL` | `debug` | `info` |
| `DATABASE_URL` | staging DB URL | production DB URL |

---

## Domain Management

```bash
# List domains
railway domain

# Add custom domain
railway domain add your-domain.com

# Get the auto-generated Railway domain
railway domain
```

---

## Logs & Debugging

```bash
# Tail live logs
railway logs --tail

# Last N lines
railway logs --tail 100

# Filter by deployment
railway logs --deployment <deployment-id>
```

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| 502 Bad Gateway | App not binding to `$PORT` | Ensure app reads `process.env.PORT` |
| Build fails | Missing dependencies | Check `package.json` / `requirements.txt` |
| Crash loop | Missing env vars | Verify all required vars are set |
| Old code deployed | Wrong branch/directory | Verify `railway link` and deploy directory |

---

## Database Operations

### Provisioning
```bash
# Add PostgreSQL
railway add --plugin postgresql

# Get connection string
railway variables get DATABASE_URL
```

### Migrations
```bash
# Run migrations (Node.js/Prisma)
railway run npx prisma migrate deploy

# Run migrations (Python/Alembic)
railway run alembic upgrade head
```

---

## Rollback

If a deploy is broken:

```bash
# 1. Check recent deployments
railway status

# 2. Rollback to previous deployment
railway rollback

# 3. Verify rollback
railway logs --tail 20
until curl -sf https://<domain>.up.railway.app/health; do sleep 2; done
```

---

## Multi-Service Deploy Pattern

For projects with multiple services (frontend + backend):

```bash
# 1. Deploy backend FIRST (API must be available for frontend)
cd server/ && railway up --detach -s backend
until curl -sf https://api-domain.up.railway.app/health; do sleep 5; done

# 2. Deploy frontend AFTER backend is healthy
cd frontend/ && railway up --detach -s frontend
until curl -sf https://frontend-domain.up.railway.app; do sleep 5; done

# 3. Smoke test
curl -s https://frontend-domain.up.railway.app | grep -q "<title>"
curl -s https://api-domain.up.railway.app/api/health | grep -q "ok"
```
