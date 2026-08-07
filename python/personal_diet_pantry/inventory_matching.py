"""Deterministic shorthand resolution limited to currently usable inventory."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
import sqlite3
import unicodedata

from . import learning
from .clock import utc_text


_ELIGIBLE_STATUSES = ("active", "opened", "thawed")
_BRACKETED = re.compile(r"[\(\[（【][^\)\]）】]*[\)\]）】]")
_UNIT_ALIASES = {
    "g": "g",
    "gram": "g",
    "grams": "g",
    "ml": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "piece": "piece",
    "pieces": "piece",
    "portion": "portion",
    "portions": "portion",
    "pack": "pack",
    "packs": "pack",
}
_SEMANTIC_GROUPS = {
    "番茄": ("番茄", "西红柿"),
    "西红柿": ("番茄", "西红柿"),
    "蘑菇": ("香菇", "平菇", "口蘑", "金针菇", "杏鲍菇"),
}
_PROCESSED_MARKERS = ("罐头", "酱", "汁", "干", "粉", "腌", "冻干", "浓缩")


@dataclass(frozen=True)
class InventorySearchCandidate:
    food_name: str
    normalized_name: str
    unit: str
    available_quantity: Decimal
    batch_count: int
    match_kind: str
    match_rank: int
    batch_ids: tuple[int, ...]
    expired_only: bool = False


class AmbiguousInventoryMatchError(ValueError):
    """Raised when shorthand identifies multiple distinct in-stock products."""

    def __init__(self, requested_name: str, candidates: tuple[str, ...]) -> None:
        super().__init__(
            "More than one in-stock product matches the supplied food name"
        )
        self.requested_name = requested_name
        self.candidates = candidates

    def __str__(self) -> str:
        return str(self.args[0])


def resolve_inventory_name(
    connection: sqlite3.Connection,
    requested_name: str,
    unit: str,
) -> str | None:
    """Return one canonical in-stock name, or ``None`` when nothing matches."""

    requested = _required_text(requested_name, "requested_name")
    normalized_unit = canonical_inventory_unit(unit)
    placeholders = ", ".join("?" for _ in _ELIGIBLE_STATUSES)
    rows = connection.execute(
        f"""
        SELECT DISTINCT normalized_name
        FROM pantry_batches
        WHERE remaining_quantity > 0
          AND status IN ({placeholders})
          AND (expires_at IS NULL OR expires_at > ?)
          AND lower(unit) = ?
        ORDER BY normalized_name
        """,
        (*_ELIGIBLE_STATUSES, utc_text(), normalized_unit),
    ).fetchall()
    names = tuple(str(row["normalized_name"]) for row in rows)
    if not names:
        return None

    requested_key = _match_key(requested)
    exact = tuple(name for name in names if _match_key(name) == requested_key)
    if exact:
        return exact[0]

    learned_alias = learning.learned_food_alias(connection, requested)
    if learned_alias is not None:
        alias_key = _match_key(learned_alias)
        alias_matches = tuple(name for name in names if _match_key(name) == alias_key)
        if alias_matches:
            return _unique_or_raise(requested, alias_matches)

    semantic = _semantic_matches(requested_key, names)
    if semantic:
        return _unique_or_raise(requested, semantic)

    containment = tuple(
        name
        for name in names
        if (
            requested_key in _match_key(name)
            or _match_key(name) in requested_key
        )
        and _food_form_compatible(requested_key, _match_key(name))
    )
    if containment:
        return _unique_or_raise(requested, containment)

    requested_core = _core_key(requested)
    core_matches = tuple(
        name
        for name in names
        if requested_core
        and _core_key(name) == requested_core
        and _food_form_compatible(requested_key, _match_key(name))
    )
    if core_matches:
        return _unique_or_raise(requested, core_matches)
    return None


def resolve_meal_inventory_name(
    connection: sqlite3.Connection,
    raw_name: str,
    normalized_name: str,
    unit: str,
) -> str | None:
    """Resolve user wording before accepting a model-normalized product name."""

    raw_match = resolve_inventory_name(connection, raw_name, unit)
    if raw_match is not None:
        return raw_match
    return resolve_inventory_name(connection, normalized_name, unit)


def search_inventory_candidates(
    connection: sqlite3.Connection,
    search_text: str,
    *,
    unit: str | None = None,
    statuses: tuple[str, ...] | None = None,
    storage_location: str | None = None,
    limit: int = 5,
    allow_expired_fallback: bool = False,
    _exclude_expired: bool = True,
) -> tuple[InventorySearchCandidate, ...]:
    """Return usable products, optionally falling back to discard-only stock."""

    requested = _required_text(search_text, "search_text")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("limit must be between 1 and 5")
    requested_key = _match_key(requested)
    normalized_unit = canonical_inventory_unit(unit) if unit is not None else None
    eligible = statuses or _ELIGIBLE_STATUSES
    candidates: list[InventorySearchCandidate] = []
    seen: set[tuple[str, str]] = set()

    def add_exact_text(text: str, match_kind: str, match_rank: int) -> None:
        if len(candidates) >= limit:
            return
        indexed_rows = _candidate_rows(
            connection,
            match_mode="indexed_exact",
            match_text=text,
            unit=normalized_unit,
            statuses=eligible,
            storage_location=storage_location,
            limit=limit,
            exclude_expired=_exclude_expired,
        )
        rows = indexed_rows
        if not rows:
            register_inventory_match_key(connection)
            rows = _candidate_rows(
                connection,
                match_mode="normalized_exact",
                match_text=text,
                unit=normalized_unit,
                statuses=eligible,
                storage_location=storage_location,
                limit=limit,
                exclude_expired=_exclude_expired,
            )
        _append_search_candidates(
            candidates,
            seen,
            rows,
            match_kind=match_kind,
            match_rank=match_rank,
            limit=limit,
            expired_only=not _exclude_expired,
        )

    add_exact_text(requested, "exact", 0)

    queried_aliases = {requested_key}
    learned = learning.learned_food_alias(connection, requested)
    if learned is not None:
        learned_key = _match_key(learned)
        if learned_key not in queried_aliases:
            queried_aliases.add(learned_key)
            add_exact_text(learned, "learned_alias", 1)

    for alias in _SEMANTIC_GROUPS.get(requested_key, ()):
        alias_key = _match_key(alias)
        if alias_key in queried_aliases:
            continue
        queried_aliases.add(alias_key)
        add_exact_text(alias, "static_alias", 1)

    if len(candidates) < limit:
        register_inventory_match_key(connection)
        keyword_rows = _candidate_rows(
            connection,
            match_mode="keyword",
            match_text=requested_key,
            unit=normalized_unit,
            statuses=eligible,
            storage_location=storage_location,
            limit=limit,
            exclude_expired=_exclude_expired,
        )
        _append_search_candidates(
            candidates,
            seen,
            keyword_rows,
            match_kind="keyword",
            match_rank=2,
            limit=limit,
            expired_only=not _exclude_expired,
        )
    if candidates or not allow_expired_fallback or not _exclude_expired:
        return tuple(candidates)
    return search_inventory_candidates(
        connection,
        requested,
        unit=normalized_unit,
        statuses=eligible,
        storage_location=storage_location,
        limit=limit,
        allow_expired_fallback=False,
        _exclude_expired=False,
    )


def canonical_inventory_unit(value: str) -> str:
    unit = _required_text(value, "unit").casefold()
    try:
        return _UNIT_ALIASES[unit]
    except KeyError as error:
        raise ValueError(f"unsupported inventory unit: {value!r}") from error


def _unique_or_raise(requested_name: str, candidates: tuple[str, ...]) -> str:
    distinct = tuple(dict.fromkeys(candidates))
    if len(distinct) == 1:
        return distinct[0]
    raise AmbiguousInventoryMatchError(requested_name, distinct)


def _semantic_matches(
    requested_key: str, names: tuple[str, ...]
) -> tuple[str, ...]:
    aliases = _SEMANTIC_GROUPS.get(requested_key)
    if aliases is None:
        return ()
    alias_keys = {_match_key(alias) for alias in aliases}
    return tuple(name for name in names if _match_key(name) in alias_keys)


def _food_form_compatible(requested_key: str, candidate_key: str) -> bool:
    requested_markers = {
        marker for marker in _PROCESSED_MARKERS if marker in requested_key
    }
    candidate_markers = {
        marker for marker in _PROCESSED_MARKERS if marker in candidate_key
    }
    if requested_markers or candidate_markers:
        return requested_markers == candidate_markers
    return True


def _core_key(value: str) -> str:
    return _match_key(_BRACKETED.sub("", value))


def _match_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith(("P", "Z"))
    )


def register_inventory_match_key(connection: sqlite3.Connection) -> None:
    connection.create_function(
        "inventory_match_key",
        1,
        _sqlite_match_key,
        deterministic=True,
    )


def _sqlite_match_key(value: object) -> str:
    return _match_key(value) if isinstance(value, str) else ""


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


def _candidate_rows(
    connection: sqlite3.Connection,
    *,
    match_mode: str,
    match_text: str,
    unit: str | None,
    statuses: tuple[str, ...],
    storage_location: str | None,
    limit: int,
    exclude_expired: bool,
) -> tuple[sqlite3.Row, ...]:
    clauses = ["remaining_quantity > 0"]
    values: list[object] = []
    select_values: list[object] = []
    placeholders = ", ".join("?" for _ in statuses)
    clauses.append(f"status IN ({placeholders})")
    values.extend(statuses)
    if exclude_expired:
        clauses.append("(expires_at IS NULL OR expires_at > ?)")
        values.append(utc_text())
    if unit is not None:
        clauses.append("unit = ? COLLATE NOCASE")
        values.append(unit)
    if storage_location is not None:
        clauses.append("storage_location = ?")
        values.append(storage_location)
    if match_mode == "indexed_exact":
        clauses.append("normalized_name = ? COLLATE NOCASE")
        values.append(match_text)
        exact_order = "0"
    elif match_mode == "normalized_exact":
        clauses.append("inventory_match_key(normalized_name) = ?")
        values.append(_match_key(match_text))
        exact_order = "0"
    elif match_mode == "keyword":
        clauses.append(
            "(instr(inventory_match_key(normalized_name), ?) > 0 "
            "OR instr(inventory_match_key(food_name), ?) > 0)"
        )
        values.extend((match_text, match_text))
        exact_order = (
            "CASE WHEN inventory_match_key(normalized_name) = ? "
            "OR inventory_match_key(food_name) = ? THEN 0 ELSE 1 END"
        )
        select_values.extend((match_text, match_text))
    else:
        raise ValueError(f"unsupported inventory search mode: {match_mode}")

    rows = connection.execute(
        f"""
        SELECT
            MIN(food_name) AS food_name,
            normalized_name,
            unit,
            SUM(remaining_quantity) AS available_quantity,
            COUNT(*) AS batch_count,
            GROUP_CONCAT(id) AS batch_ids,
            MIN({exact_order}) AS exactness
        FROM pantry_batches
        WHERE {' AND '.join(clauses)}
        GROUP BY normalized_name, unit
        ORDER BY exactness, normalized_name COLLATE NOCASE, unit COLLATE NOCASE
        LIMIT ?
        """,
        (*select_values, *values, limit),
    ).fetchall()
    return tuple(rows)


def _search_candidates(
    rows: tuple[sqlite3.Row, ...],
    match_kind: str,
    match_rank: int,
    *,
    expired_only: bool,
) -> tuple[InventorySearchCandidate, ...]:
    return tuple(
        InventorySearchCandidate(
            food_name=str(row["food_name"]),
            normalized_name=str(row["normalized_name"]),
            unit=str(row["unit"]),
            available_quantity=Decimal(str(row["available_quantity"])),
            batch_count=int(row["batch_count"]),
            match_kind=match_kind,
            match_rank=match_rank,
            batch_ids=tuple(
                int(value) for value in str(row["batch_ids"]).split(",")
            ),
            expired_only=expired_only,
        )
        for row in rows
    )


def _append_search_candidates(
    output: list[InventorySearchCandidate],
    seen: set[tuple[str, str]],
    rows: tuple[sqlite3.Row, ...],
    *,
    match_kind: str,
    match_rank: int,
    limit: int,
    expired_only: bool,
) -> None:
    for candidate in _search_candidates(
        rows,
        match_kind,
        match_rank,
        expired_only=expired_only,
    ):
        identity = (
            candidate.normalized_name.casefold(),
            candidate.unit.casefold(),
        )
        if identity in seen:
            continue
        seen.add(identity)
        output.append(candidate)
        if len(output) >= limit:
            return
