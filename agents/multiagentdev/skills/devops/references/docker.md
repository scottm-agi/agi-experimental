# Docker Build & Registry Reference

## Overview

This reference covers building Docker images on Apple Silicon (M-series) Macs for
linux/amd64 deployment targets, tagging conventions, and pushing to GitHub Container
Registry (GHCR).

---

## Build & Tag

### Multi-Platform Build (Apple Silicon → linux/amd64)

**Critical**: Apple Silicon Macs MUST use Rosetta 2 emulation via Docker Desktop,
NOT QEMU. QEMU builds are 10-50x slower and frequently hang.

#### Prerequisites
```bash
# Verify Docker Desktop has Rosetta enabled
# Docker Desktop → Settings → General → ✅ "Use Rosetta for x86_64/amd64 emulation"

# Verify buildx is available
docker buildx version
```

#### Build Commands
```bash
# Standard amd64 build
docker build --platform linux/amd64 -t ghcr.io/${ORG}/${REPO}:latest .

# With date-stamped tag
DATE_TAG=$(date +%Y%m%d-%H%M%S)
docker build --platform linux/amd64 \
  -t ghcr.io/${ORG}/${REPO}:latest \
  -t ghcr.io/${ORG}/${REPO}:${DATE_TAG} \
  .

# Multi-stage build with explicit target
docker build --platform linux/amd64 \
  --target production \
  -t ghcr.io/${ORG}/${REPO}:latest \
  .
```

#### Build Verification
```bash
# Verify architecture
docker inspect ghcr.io/${ORG}/${REPO}:latest | grep Architecture
# Expected: "Architecture": "amd64"

# Verify image size is reasonable
docker images ghcr.io/${ORG}/${REPO}:latest --format "{{.Size}}"
```

---

## Push to GHCR

### Authentication
```bash
# Login to GHCR (use PAT with packages:write scope)
echo $GHCR_TOKEN | docker login ghcr.io -u $GHCR_USERNAME --password-stdin
```

### Push
```bash
# Push all tags
docker push ghcr.io/${ORG}/${REPO}:latest
docker push ghcr.io/${ORG}/${REPO}:${DATE_TAG}
```

### Post-Push Verification
```bash
# Verify image exists in registry
docker manifest inspect ghcr.io/${ORG}/${REPO}:latest

# Or via GitHub API
gh api /orgs/${ORG}/packages/container/${REPO}/versions --jq '.[0].metadata.container.tags'
```

---

## Disk Space Management

Before building, check available disk space:
```bash
# Check Docker disk usage
docker system df

# Clean up if needed (CONFIRM WITH USER FIRST)
docker system prune -f        # Remove dangling images
docker builder prune -f       # Clear build cache
```

---

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| Build hangs on Apple Silicon | Using QEMU instead of Rosetta | Enable Rosetta in Docker Desktop settings |
| `denied: permission denied` on push | Bad GHCR token or wrong org | Regenerate PAT with `packages:write` scope |
| Image runs on Mac but crashes on server | Architecture mismatch | Always use `--platform linux/amd64` |
| `no space left on device` | Docker disk full | Run `docker system prune` (with user confirmation) |

---

## Tagging Convention

| Tag | Purpose |
|-----|---------|
| `latest` | Current production image |
| `YYYYMMDD-HHMMSS` | Date-stamped for rollback |
| `v1.0.0` | Semantic version (for releases) |
| `sha-<commit>` | Git commit SHA (for traceability) |
