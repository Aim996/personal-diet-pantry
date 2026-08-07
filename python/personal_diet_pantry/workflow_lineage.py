"""Opaque workflow-to-entity lineage used by privacy-safe cleanup."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Iterable


@dataclass(frozen=True)
class EntityLink:
    entity_kind: str
    entity_key: str
    relation: str


def _entries(snapshot: str, relation: str) -> tuple[EntityLink, ...]:
    try:
        parsed = json.loads(snapshot)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("transaction snapshot must be valid JSON") from error
    if not isinstance(parsed, list):
        raise ValueError("transaction snapshot must be an array")
    links: list[EntityLink] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        table = entry.get("table")
        row_id = entry.get("row_id")
        if isinstance(table, str) and table.strip() and row_id is not None:
            links.append(EntityLink(table, str(row_id), relation))
    return tuple(links)


def _insert_links(
    connection: sqlite3.Connection,
    *,
    workflow_kind: str,
    workflow_key: str,
    links: Iterable[EntityLink],
    created_at: str,
) -> None:
    connection.executemany(
        """
        INSERT OR IGNORE INTO workflow_entity_links (
            workflow_kind, workflow_key, entity_kind, entity_key,
            relation, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            (
                workflow_kind,
                workflow_key,
                link.entity_kind,
                link.entity_key,
                link.relation,
                created_at,
            )
            for link in links
        ),
    )


def index_transaction_snapshots(
    connection: sqlite3.Connection,
    *,
    transaction_id: str,
    before_snapshot: str,
    after_snapshot: str,
    created_at: str,
) -> None:
    _insert_links(
        connection,
        workflow_kind="transaction",
        workflow_key=transaction_id,
        links=(
            *_entries(before_snapshot, "before"),
            *_entries(after_snapshot, "after"),
        ),
        created_at=created_at,
    )


def index_preview_links(
    connection: sqlite3.Connection,
    *,
    token_hash: str,
    links: Iterable[EntityLink],
    created_at: str,
) -> None:
    _insert_links(
        connection,
        workflow_kind="preview",
        workflow_key=token_hash,
        links=links,
        created_at=created_at,
    )


def workflow_keys_for_entities(
    connection: sqlite3.Connection,
    entities: Iterable[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    found: set[tuple[str, str]] = set()
    for entity_kind, entity_key in entities:
        found.update(
            (str(row["workflow_kind"]), str(row["workflow_key"]))
            for row in connection.execute(
                """
                SELECT workflow_kind, workflow_key
                FROM workflow_entity_links
                WHERE entity_kind = ? AND entity_key = ?
                """,
                (entity_kind, entity_key),
            )
        )
    return tuple(sorted(found))
