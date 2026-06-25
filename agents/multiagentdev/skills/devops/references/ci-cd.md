# CI/CD Pipeline Reference

## Overview

This reference covers setting up continuous integration and continuous deployment
pipelines using GitHub Actions, Forgejo Actions, and automated testing workflows.

---

## GitHub Actions

### Standard CI Pipeline

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
      
      - name: Install dependencies
        run: npm ci
      
      - name: Lint
        run: npm run lint
      
      - name: Type check
        run: npm run type-check
      
      - name: Unit tests
        run: npm test
      
      - name: Build
        run: npm run build

  e2e:
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
      
      - name: Install dependencies
        run: npm ci
      
      - name: Start server
        run: npm start &
        env:
          PORT: ${{ vars.APP_PORT || '3000' }}
          NODE_ENV: test
      
      - name: Wait for server
        run: |
          until curl -sf http://localhost:${{ vars.APP_PORT || '3000' }}/health; do
            sleep 2
          done
      
      - name: Run E2E tests
        run: npm run test:e2e
```

### Python CI Pipeline

```yaml
name: Python CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.11', '3.12']
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install -r requirements-dev.txt
      
      - name: Lint
        run: ruff check .
      
      - name: Type check
        run: mypy .
      
      - name: Test
        run: pytest --cov=. --cov-report=xml
```

### Docker Build & Push Action

```yaml
name: Build and Push Docker Image

on:
  push:
    branches: [main]
    tags: ['v*']

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          platforms: linux/amd64
          tags: |
            ghcr.io/${{ github.repository }}:latest
            ghcr.io/${{ github.repository }}:${{ github.sha }}
```

### Railway Auto-Deploy Action

```yaml
name: Deploy to Railway

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    needs: test  # Only deploy after tests pass
    steps:
      - uses: actions/checkout@v4
      
      - name: Install Railway CLI
        run: npm i -g @railway/cli
      
      - name: Deploy
        run: railway up --detach
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
      
      - name: Verify deployment
        run: |
          sleep 30  # Initial wait for deploy to start
          until curl -sf ${{ vars.HEALTH_URL }}; do
            echo "Waiting for deployment..."
            sleep 10
          done
          echo "Deployment verified!"
```

---

## Forgejo Actions

Forgejo Actions are compatible with GitHub Actions with minor differences:

### Key Differences from GitHub
- Use `runs-on: docker` instead of `runs-on: ubuntu-latest`
- Secrets are configured in Forgejo → Repository → Settings → Actions → Secrets
- Runner must be self-hosted (Forgejo doesn't provide hosted runners)

### Standard Forgejo CI

Create `.forgejo/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: docker
    container:
      image: node:20
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: npm test
      - run: npm run build
```

---

## Secrets Management

### GitHub Actions Secrets
```bash
# Set via CLI
gh secret set RAILWAY_TOKEN --body "your-token-here"
gh secret set DATABASE_URL --body "postgresql://..."

# List secrets
gh secret list
```

### Required Secrets

| Secret | Purpose | Where to Get |
|--------|---------|-------------|
| `RAILWAY_TOKEN` | Railway deploy auth | Railway Dashboard → Account → Tokens |
| `GHCR_TOKEN` | Container registry push | GitHub → Settings → Developer Settings → PATs |
| `DATABASE_URL` | Database connection | Railway → Service → Variables |

---

## Pipeline Patterns

### Trunk-Based Development
```
main branch → CI tests → auto-deploy to staging
tag v*.*.* → CI tests → auto-deploy to production
```

### Feature Branch Workflow
```
feature/* → CI tests (no deploy)
PR to main → CI tests + preview deploy
merge to main → CI tests → deploy to staging
manual promotion → deploy to production
```

### Monorepo Pipeline
```yaml
on:
  push:
    paths:
      - 'frontend/**'    # Only trigger on frontend changes
      - 'server/**'      # Only trigger on server changes
```

---

## Post-Deploy Verification

Every pipeline MUST include post-deploy verification:

1. **Health check** — HTTP 200 from health endpoint
2. **Smoke test** — Core functionality responds correctly
3. **SHA verification** — Deployed version matches expected commit
4. **Alert on failure** — Notify team if verification fails
