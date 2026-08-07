from __future__ import annotations

import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from personal_diet_pantry.service import DietService  # noqa: E402


def main() -> int:
    data_dir = Path(sys.argv[1])
    checkpoint = sys.argv[2]
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(data_dir)},
        env={},
    ) as service:
        if len(sys.argv) > 3 and sys.argv[3] == "with-unrelated":
            service.connection.execute(
                """
                INSERT OR IGNORE INTO privacy_erasure_tombstones (
                    erasure_handle, preview_token_hash, scope,
                    affected_counts_json, summary_sha256, committed_at,
                    control_operation_handle
                ) VALUES (?, ?, 'all_business', '{}', ?, ?, ?)
                """,
                (
                    "erase_unrelated_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "d" * 64,
                    "e" * 64,
                    "2026-07-30T00:00:00Z",
                    "mop_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                ),
            )
            service.connection.commit()
        service.dispatch(
            {
                "domain": "water",
                "action": "record",
                "payload": {
                    "amount": "250",
                    "unit": "ml",
                    "occurred_at": "2026-07-30T01:00:00Z",
                    "source_text": "crash canary",
                },
            }
        )
        (service.data_paths.cache / "same.bin").write_bytes(b"cache-content")
        nested = service.data_paths.reports / "nested" / "same.bin"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_bytes(b"report-content")
        preview = service.dispatch(
            {
                "domain": "system",
                "action": "preview_delete_data",
                "payload": {"scope": "all_business"},
            }
        )

        def crash(observed: str) -> None:
            if observed == checkpoint:
                os._exit(91)

        service.trusted_workflows._crash_probe = crash
        service.dispatch(
            {
                "domain": "system",
                "action": "commit_delete_data",
                "payload": {
                    "commit_handle": preview["data"]["workflow"][
                        "commit_handle"
                    ],
                    "confirmed": True,
                    "operation_key": f"crash-{checkpoint}",
                },
            }
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
