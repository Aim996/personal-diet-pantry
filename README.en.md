[简体中文](README.md) | **English**

# Personal Diet Pantry

Personal Diet Pantry `v0.7.5.2` is a local-first OpenClaw skill for one person's meals, nutrition, hydration, body weight, pantry, cooking, leftovers, corrections, and reports. The technical package version is `0.9.2`. SQLite in an external `dataDir` is the formal source of truth.

## Current status

This tree is a local v0.7.5.2 development candidate. It has not yet created a Git tag, GitHub Release, or OpenClaw deployment; build and publication do not auto-install, enable, restart, or configure any instance.

This version adds migration 022 for pantry storage/expiry provenance. Clear single-domain completed facts write directly; vague portions preview once; ordinary pantry intake no longer requires production or expiry dates and receives marked backend defaults. Corn edible-weight behavior, deterministic six-metric meal receipts, compact plain-water receipts, expired-stock filtering, read-only query safety, the seven public tool families, and database transactions remain protected.

The previous stable release and its downloaded-asset verification remain documented in the [v0.7.4.28 public release record](docs/releases/v0.7.4.28.zh-CN.md) (Chinese). The v0.7.5.2 release must produce its own independent gate and asset evidence.

## Install boundary

After a formal release exists, verify `SHA256SUMS` and install only `personal-diet-pantry-0.7.5.2-installable.tgz` through OpenClaw's npm-pack path. The source archive is for review and reproduction, not plugin installation.

```text
openclaw plugins install npm-pack:/path/to/personal-diet-pantry-0.7.5.2-installable.tgz
openclaw plugins enable personal-diet-pantry
openclaw gateway restart
openclaw plugins inspect personal-diet-pantry --runtime --json
```

Requirements: OpenClaw `>=2026.5.17`, Node.js `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`, Python `>=3.11,<4`, and a dedicated persistent `dataDir` outside the source checkout. After installation, independently confirm all seven tools. Initialize a new ledger only with explicit user authorization, then run self-check and a zero-business-write acceptance. See [the concise install guide](docs/INSTALL.md) and [the detailed Chinese installation manual](docs/INSTALLATION.zh-CN.md).

## Data safety and upgrades

Never point tests at an existing OpenClaw state or personal data directory. Databases, backups, exports, reports, credentials, host information, addresses, and real meal or body-weight data must not enter Git or release artifacts.

Upgrading from v0.7.5.0 to v0.7.5.2 adds no migration and keeps the existing external `dataDir`; a verified cold backup is still recommended. A direct upgrade from v0.7.4.28 applies the existing migration 022, so rollback requires restoring the pre-upgrade cold backup before reinstalling v0.7.4.28. An online `diet_system backup` is only for same-version recovery. See [the upgrade entry](docs/UPGRADING.md).

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

The formal builder creates an immutable directory containing `personal-diet-pantry-0.7.5.2-source.tar.gz`, `personal-diet-pantry-0.7.5.2-installable.tgz`, `release-manifest.json`, `TEST-SUMMARY-v0.7.5.2.zh-CN.md`, `SHA256SUMS`, and a local `GitHub文档/` audit tree. It refuses an existing destination and does not publish remotely. Maintainer steps are documented in [docs/RELEASING.md](docs/RELEASING.md).

## License

MIT. See [LICENSE](LICENSE).
