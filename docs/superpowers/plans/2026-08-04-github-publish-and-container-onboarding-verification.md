# GitHub Publish and Container Onboarding Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish v0.7.3 to a private GitHub repository and prove that a fresh non-root user can install and initialize the packaged plugin inside Docker.

**Architecture:** CI builds the npm-pack artifact from the audited source commit. A separate Docker stage receives only that artifact, installs it through the OpenClaw plugin lifecycle, verifies runtime tool registration, and calls the packaged TypeScript-to-Python bridge for initialization and self-check.

**Tech Stack:** GitHub Actions, Docker, Node.js 24, OpenClaw 2026.7.1-2, Python 3, SQLite, Bash.

## Global Constraints

- Repository visibility is private.
- Product version remains 0.7.3.
- Runtime requirements remain OpenClaw 2026.5.17+, Node.js 22.22.3+, and Python `>=3.11,<4`.
- Docker verification must run as UID/GID 12001 and must not use `--dangerously-force-unsafe-install`.
- No database, report, backup, export, cache, credential, or personal data may be committed or uploaded as an artifact.

---

### Task 1: Repository hygiene and publication metadata

**Files:**
- Create: `.gitignore`
- Modify: `README.md`
- Verify: `scripts/scan_sensitive_content.py`

**Interfaces:**
- Consumes: the v0.7.3 source archive.
- Produces: a clean Git tree suitable for a private GitHub repository.

- [ ] **Step 1:** Run `python scripts/scan_sensitive_content.py .` and require a zero exit code.
- [ ] **Step 2:** Add ignore rules for dependencies, Python caches, virtual environments, environment files, generated release archives, SQLite files, reports, backups, imports, exports, and caches.
- [ ] **Step 3:** Add a README section that identifies the Docker workflow as a first-user installation test rather than a production deployment image.
- [ ] **Step 4:** Run the sensitive-content scan again and inspect the complete Git file list.

### Task 2: Fresh-user Docker verification

**Files:**
- Create: `docker/first-user/Dockerfile`
- Create: `docker/first-user/verify.sh`
- Reuse: `scripts/bridge_probe.mjs`

**Interfaces:**
- Consumes: `personal-diet-pantry-0.7.3.tgz` at Docker build time.
- Produces: exit status 0 plus a bounded `verification-summary.json` only when installation and initialization succeed.

- [ ] **Step 1:** Build an image based on `node:24-bookworm` with Python, OpenClaw 2026.7.1-2, and the two locked Python dependencies.
- [ ] **Step 2:** Create and switch to user `newcomer` with UID/GID 12001 before any OpenClaw state or diet data is created.
- [ ] **Step 3:** Install the npm-pack through OpenClaw, set the isolated `dataDir`, enable the plugin, and validate the resulting config.
- [ ] **Step 4:** Run runtime inspection and fail unless all seven public tool names are present.
- [ ] **Step 5:** Locate the managed installed package, run `bridge_probe.mjs` in smoke mode, reject any self-check `FAIL`, and require `diet.sqlite` in the isolated data directory.
- [ ] **Step 6:** Confirm excluded source/test directories are absent from the installed package.

### Task 3: GitHub Actions Docker execution

**Files:**
- Create: `.github/workflows/docker-first-user.yml`

**Interfaces:**
- Consumes: the repository commit and Docker verifier from Task 2.
- Produces: a required, reproducible CI result and a non-sensitive text summary artifact.

- [ ] **Step 1:** Configure checkout, Node 24, Python 3.12, `npm ci`, `npm run build`, and `npm pack`.
- [ ] **Step 2:** Verify the generated tarball filename and build the first-user image with `--no-cache`.
- [ ] **Step 3:** Run the container without privileged mode and copy out only `verification-summary.json`.
- [ ] **Step 4:** Upload the bounded summary artifact even when the Docker step fails; never upload the data directory.
- [ ] **Step 5:** Run local source tests where available, push the repository, wait for Actions, and inspect the final workflow conclusion.

