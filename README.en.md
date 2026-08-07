[简体中文](README.md) | **English**

# Personal Diet Pantry

Personal Diet Pantry `v0.7.4.28` is a local-first OpenClaw skill for one person's meals, nutrition, hydration, body weight, pantry, cooking, leftovers, corrections, and reports. The technical package version is `0.8.28`. SQLite in an external `dataDir` is the formal source of truth.

## Current status

This is a public stable release. Download the pinned assets from [GitHub Release v0.7.4.28](https://github.com/Aim996/personal-diet-pantry/releases/tag/v0.7.4.28). Publishing the repository and Release does not auto-install, enable, restart, or configure any OpenClaw instance.

After publication, all five GitHub assets were downloaded again and hash-matched against the immutable local candidate. The tagged commit, local gate results, asset evidence, and the GitHub Actions infrastructure limitation are recorded in the [v0.7.4.28 public release record](docs/releases/v0.7.4.28.zh-CN.md) (Chinese).

This version adds no migration and keeps migrations 001–021. A pure recent-operation question such as “did that just get recorded?” now has one bounded route: one recent transaction read, with Meal, Pantry, Report, repeat Diet reads, and write replay blocked for that run. Agent-facing read text prefers the tool's local timestamps and omits a competing UTC companion when both exist, while the original structured details and UTC database values remain unchanged. Explicit conditional backfill, package/serving nutrition, corn edible-weight behavior, deterministic six-metric meal receipts, compact plain-water receipts, expired-stock filtering, the seven public tool families, forty daily-default actions, the full seventy-five-action typed contract, and database transactions remain unchanged.

## Install boundary

Verify `SHA256SUMS` and install only `personal-diet-pantry-0.7.4.28-installable.tgz` through OpenClaw's npm-pack path. The source archive is for review and reproduction, not plugin installation.

```text
openclaw plugins install npm-pack:/path/to/personal-diet-pantry-0.7.4.28-installable.tgz
openclaw plugins enable personal-diet-pantry
openclaw gateway restart
openclaw plugins inspect personal-diet-pantry --runtime --json
```

Requirements: OpenClaw `>=2026.5.17`, Node.js `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`, Python `>=3.11,<4`, and a dedicated persistent `dataDir` outside the source checkout. After installation, independently confirm all seven tools. Initialize a new ledger only with explicit user authorization, then run self-check and a zero-business-write acceptance. See [the concise install guide](docs/INSTALL.md) and [the detailed Chinese installation manual](docs/INSTALLATION.zh-CN.md).

## Data safety and upgrades

Never point tests at an existing OpenClaw state or personal data directory. Databases, backups, exports, reports, credentials, host information, addresses, and real meal or body-weight data must not enter Git or release artifacts.

Before upgrading, stop the target instance and create a verified **pre-upgrade cold backup** with the source-shipped helper. Version 0.7.4.28 keeps the v0.7.4.19 schema, so a technical code rollback can reinstall v0.7.4.19 against the preserved `dataDir`; the cold backup remains the recovery boundary for data or environment damage. Version 0.7.4.19 is a documented real-UAT failure and is not a product acceptance baseline. An online `diet_system backup` is only for same-version recovery and does not replace the pre-upgrade cold backup. See [the upgrade entry](docs/UPGRADING.md).

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

The formal builder creates an immutable directory containing `personal-diet-pantry-0.7.4.28-source.tar.gz`, `personal-diet-pantry-0.7.4.28-installable.tgz`, `release-manifest.json`, `TEST-SUMMARY-v0.7.4.28.zh-CN.md`, `SHA256SUMS`, and a local `GitHub文档/` audit tree. It refuses an existing destination and does not publish remotely. Maintainer steps are documented in [docs/RELEASING.md](docs/RELEASING.md).

## License

MIT. See [LICENSE](LICENSE).
