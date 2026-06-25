---
name: devops
description: >
  Infrastructure, deployment, and CI/CD orchestration. Uses route-first architecture
  with modular reference documents for Docker builds, Railway deployment, and CI/CD
  pipelines. Activates for infrastructure provisioning, container management,
  deployment workflows, and monitoring setup.
triggers:
  - Docker
  - CI/CD
  - deploy
  - deployment
  - Railway
  - infrastructure
  - container
  - Dockerfile
  - monitoring
  - GHCR
  - GitHub Actions
  - pipeline
  - staging
  - production
  - DevOps
trigger_patterns:
  - 'deploy.*to.*(railway|production|staging)'
  - 'docker.*(build|image|compose|push)'
  - '(ci|cd|ci/cd).*pipeline'
  - 'github.*action'
  - 'container.*registry'
  - 'ghcr.*push'
  - 'infrastructure.*setup'
anti_triggers:
  - landing page
  - UI design
  - CSS styling
  - React component
  - frontend styling
  - build a website
  - user interface
  - responsive layout
skill_type: task_specific
---

# DevOps & Infrastructure Orchestration Skill

This skill uses **route-first architecture** modeled after the official Railway Claude plugin.
Instead of monolithic instructions, it routes to specific reference documents based on intent.

> **Architecture**: SKILL.md is a routing table. Real content lives in `references/`.
> Only load the reference(s) relevant to the current task.

---

## Routing Table

| Intent | Reference | When to Load |
|--------|-----------|-------------|
| Build Docker images, push to GHCR | [`references/docker.md`](references/docker.md) | Docker build, container registry, image management |
| Deploy to Railway | [`references/railway.md`](references/railway.md) | Railway deployment, service management, environment config |
| CI/CD pipeline setup | [`references/ci-cd.md`](references/ci-cd.md) | GitHub Actions, Forgejo Actions, automated testing pipelines |

### How to Use This Routing Table

1. **Read the user's request** — Identify which infrastructure domain(s) it touches
2. **Load ONLY the relevant reference(s)** using `view_skill` or by reading the file
3. **Follow the reference's step-by-step instructions** — Each reference is self-contained
4. **For multi-step workflows**, load references in sequence (e.g., Docker → Railway for build-then-deploy)

---

## Prerequisites — Preflight Checks

Before executing ANY infrastructure operation, verify:

### Environment Verification
```bash
# Docker available?
docker --version

# Railway CLI available?
railway --version

# GitHub CLI available?
gh --version

# Authenticated to GHCR?
echo $GHCR_TOKEN | docker login ghcr.io -u $GHCR_USERNAME --password-stdin
```

### Required Environment Variables
| Variable | Purpose | Where Set |
|----------|---------|-----------|
| `GHCR_TOKEN` | GitHub Container Registry auth | GitHub Settings → Developer settings → PAT |
| `GHCR_USERNAME` | GHCR username or org | Usually the GitHub org name |
| `RAILWAY_TOKEN` | Railway CLI authentication | Railway Dashboard → Account → Tokens |

**Gate**: Do NOT proceed if preflight checks fail. Report missing prerequisites to the user.

---

## Execution Rules

### Safety-First Operations

1. **NEVER run destructive operations without user confirmation**:
   - `railway delete`
   - `docker system prune`
   - Database migrations in production
   - Environment variable changes in production

2. **Always verify BEFORE and AFTER**:
   - Before deploy: Check build passes, tests pass, environment variables set
   - After deploy: Poll health endpoint until 200, verify deployed SHA matches expected

3. **No timing-based systems** — Use health endpoint polling, not `sleep()`:
   ```bash
   # ✅ CORRECT: Poll until healthy
   until curl -sf https://your-app.up.railway.app/health; do sleep 2; done
   
   # ❌ WRONG: Arbitrary delay
   sleep 60 && curl https://your-app.up.railway.app/health
   ```

4. **Idempotent operations** — All deploy scripts must be safe to re-run

### Error Recovery

- If a deploy fails: Check logs (`railway logs`), fix the issue, re-deploy
- If a Docker build fails: Check Dockerfile, verify base image, check multi-platform args
- If CI/CD fails: Check the action logs, verify secrets are set, check runner compatibility

---

## Composition with Other Skills

- **Docker build + Railway deploy**: Load `docker.md` first, then `railway.md`
- **Full-stack deploy**: Use `fullstack-dev` skill for the build, then this skill for deploy
- **API deploy**: Use `api-backend` skill for the build, then this skill for deploy

---

## Quick Reference: Common Workflows

### Build & Push Docker Image
```
1. Load references/docker.md
2. Follow "Build & Tag" → "Push to GHCR" sections
```

### Deploy to Railway
```
1. Load references/railway.md
2. Follow "Deploy" → "Verify" sections
```

### Set Up CI/CD Pipeline
```
1. Load references/ci-cd.md
2. Follow "GitHub Actions" section for template
3. Configure secrets in repository settings
```
