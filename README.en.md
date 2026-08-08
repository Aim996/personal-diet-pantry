[简体中文](README.md) | **English**

# Personal Diet Pantry

Personal Diet Pantry `v0.7.5.4` is a local-first OpenClaw skill for one person's meals, nutrition, hydration, body weight, pantry, cooking, leftovers, corrections, and reports. The technical package version is `0.9.4`. SQLite in an external `dataDir` is the formal source of truth.

## Current status

This tree is a local v0.7.5.4 development candidate. It has not yet created a Git tag, GitHub Release, or OpenClaw deployment; build and publication do not auto-install, enable, restart, or configure any instance.

This version adds no migration and keeps migrations 001–023. It fixes stable same-session meal correction identity, partial nutrition estimates with genuinely unknown fields, truthful recording of stated expired inventory, and Docker foreground-gateway restart verification. Clear completed facts, corn edible-weight behavior, deterministic six-metric meal receipts, compact plain-water receipts, expired-stock filtering, read-only query safety, and the seven public tool families remain protected.

The released v0.7.5.3 remains the read-only rollback baseline. The v0.7.5.4 release must produce independent gate and asset evidence. Older stable-release evidence remains documented in the [v0.7.4.28 public release record](docs/releases/v0.7.4.28.zh-CN.md) (Chinese).

## Install boundary

After a formal release exists, verify `SHA256SUMS` and install only `personal-diet-pantry-0.7.5.4-installable.tgz` through OpenClaw's npm-pack path. The source archive is for review and reproduction, not plugin installation.

```text
openclaw plugins install npm-pack:/path/to/personal-diet-pantry-0.7.5.4-installable.tgz
openclaw plugins enable personal-diet-pantry
openclaw gateway restart
openclaw plugins inspect personal-diet-pantry --runtime --json
```

For a Docker foreground gateway, an in-container `openclaw gateway restart` may only print guidance. Use the instance's container manager and verify that the gateway PID or process start time actually changes; a successful package install alone does not prove the new runtime is active.

Requirements: OpenClaw `>=2026.5.17`, Node.js `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`, Python `>=3.11,<4`, and a dedicated persistent `dataDir` outside the source checkout. After installation, independently confirm all seven tools. Initialize a new ledger only with explicit user authorization, then run self-check and a zero-business-write acceptance. See [the concise install guide](docs/INSTALL.md) and [the detailed Chinese installation manual](docs/INSTALLATION.zh-CN.md).

## Data safety and upgrades

Never point tests at an existing OpenClaw state or personal data directory. Databases, backups, exports, reports, credentials, host information, addresses, and real meal or body-weight data must not enter Git or release artifacts.

Upgrading from v0.7.5.3 to v0.7.5.4 adds no migration and keeps the existing external `dataDir` and migrations 001–023. A verified pre-upgrade cold backup is still recommended; online `diet_system backup` does not replace that pre-upgrade cold backup. A normal code rollback may reinstall v0.7.5.3 against the same schema; restore the cold backup first if data integrity is in doubt. See [the upgrade entry](docs/UPGRADING.md).

## Development

Start with the [GitHub update and release workflow](GITHUB-WORKFLOW.zh-CN.md), then read [CONTRIBUTING.md](CONTRIBUTING.md) and the [protected behavior invariants](docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md) before changing the project. Use disposable data directories and run both Python and TypeScript gates.

```text
python -m pytest -q
npm run build
node node_modules/vitest/vitest.mjs run
python scripts/scan_sensitive_content.py .
python scripts/release_audit.py .
npm pack --dry-run --json
```

The formal builder creates an immutable directory containing `personal-diet-pantry-0.7.5.4-source.tar.gz`, `personal-diet-pantry-0.7.5.4-installable.tgz`, `release-manifest.json`, `TEST-SUMMARY-v0.7.5.4.zh-CN.md`, `SHA256SUMS`, and a local `GitHub文档/` audit tree. It refuses an existing destination and does not publish remotely. Maintainer steps are documented in [docs/RELEASING.md](docs/RELEASING.md).

## License

MIT. See [LICENSE](LICENSE).
