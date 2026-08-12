"""Load messy workbooks into Postgres without losing where anything came from.

Spreadsheets become `sheets.*` tables so the audited validator and executor from C-5 can
serve them, but they do not stop being spreadsheets. Every ingested row carries the
workbook, the sheet, the row number and the A1 range it came from, and those columns are
what let an answer say *RIC_Q1.xlsx › Northern › row 14 (B14:F14)* instead of quoting a
number nobody can check against the file. The reference implementation's loader stored no
provenance at all and could not cite a row.

**Re-ingest is atomic.** Each load builds a fresh table and swaps it in, inside one
transaction. Postgres makes DDL transactional, so a load that falls over half way leaves
the previous data exactly where it was. The reference dropped the table first and then
loaded, which means a failure left no data at all -- the state you are least able to
recover from and most likely to be in during a demo. The swap also handles a workbook
whose columns have changed, which `CREATE TABLE IF NOT EXISTS` silently would not.

The generation is the workbook's content hash, recorded on every row. Keyed on content
rather than on a clock, so re-running an ingest over an unchanged file is recognisable as
the same load rather than looking like new data.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import sql

from vericlaim.sheets.coercion import coerce, postgres_type
from vericlaim.sheets.profiler import (
    SheetProfile,
    TableProfile,
    normalize_name,
    profile_workbook,
)
from vericlaim.sql.db import Database

SHEETS_SCHEMA = "sheets"

# The provenance every ingested row carries. Prefixed so they cannot collide with a column
# an analyst named, and so a schema context can describe them as lineage rather than data.
LINEAGE_COLUMNS = ("_workbook", "_sheet", "_row", "_a1_range", "_generation")
LINEAGE_TYPES = {
    "_workbook": "TEXT",
    "_sheet": "TEXT",
    "_row": "INTEGER",
    "_a1_range": "TEXT",
    "_generation": "TEXT",
}

GENERATION_LENGTH = 12
# Postgres truncates identifiers at 63 bytes. Truncating here instead means the name we
# record is the name the database has.
MAX_IDENTIFIER = 63


@dataclass(frozen=True, slots=True)
class IngestedColumn:
    name: str
    kind: str
    type: str


@dataclass(frozen=True, slots=True)
class IngestedTable:
    """One table loaded from one table on one sheet."""

    schema: str
    table: str
    workbook: str
    sheet: str
    title: str | None
    columns: tuple[IngestedColumn, ...]
    row_count: int
    generation: str
    a1_range: str

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


def table_name(workbook: str, sheet: str, index: int) -> str:
    """Name the table after the workbook and the sheet it came from.

    Both, because two workbooks each with a "Summary" sheet must not collide, and because
    the name appears in a schema context somebody reviews and in the SQL an answer cites.
    """
    stem = normalize_name(Path(workbook).stem, fallback="workbook")
    leaf = normalize_name(sheet, fallback="sheet")
    name = f"{stem}__{leaf}"
    if index:
        name = f"{name}_{index + 1}"
    return name[:MAX_IDENTIFIER]


def generation_for(path: Path | str) -> str:
    """A short hash of the workbook's contents, recorded on every row it produces."""
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return digest[:GENERATION_LENGTH]


def rows_for(
    profile: SheetProfile, table: TableProfile, generation: str
) -> list[tuple[Any, ...]]:
    """Turn one profiled table into the rows that will be inserted.

    Each row is its coerced values followed by its lineage. Footer rows never reach here:
    the profiler already separated them, because a TOTAL line ingested as data doubles
    every aggregate and invents an entity called TOTAL.
    """
    rows: list[tuple[Any, ...]] = []
    for row in range(table.first_data_row, table.last_data_row + 1):
        values = [
            coerce(table.value_at(row, _column_index(column.letter)), column.kind).value
            for column in table.columns
        ]
        rows.append(
            (
                *values,
                profile.workbook,
                profile.sheet,
                row,
                table.row_range(row),
                generation,
            )
        )
    return rows


def _column_index(letter: str) -> int:
    index = 0
    for character in letter:
        index = index * 26 + (ord(character.upper()) - ord("A") + 1)
    return index


def ingest_workbook(
    db: Database,
    path: Path | str,
    *,
    profiles: Sequence[SheetProfile] | None = None,
    rows_for: Callable[..., list[tuple[Any, ...]]] = rows_for,
) -> tuple[IngestedTable, ...]:
    """Load every table in a workbook, replacing any previous load of it.

    ``profiles`` and ``rows_for`` are injectable so a test can drive the failure path
    without corrupting a real workbook. Everything happens in one transaction: either the
    whole workbook lands or the database is untouched.
    """
    path = Path(path)
    generation = generation_for(path)
    sheets = profiles if profiles is not None else profile_workbook(path)

    ingested: list[IngestedTable] = []
    with db.connection() as conn:
        conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(SHEETS_SCHEMA)
            )
        )
        for profile in sheets:
            for index, table in enumerate(profile.tables):
                name = table_name(profile.workbook, profile.sheet, index)
                rows = rows_for(profile, table, generation)
                _load(conn, name, table, rows)
                ingested.append(
                    IngestedTable(
                        schema=SHEETS_SCHEMA,
                        table=name,
                        workbook=profile.workbook,
                        sheet=profile.sheet,
                        title=table.title,
                        columns=tuple(
                            IngestedColumn(
                                name=column.name,
                                kind=column.kind,
                                type=postgres_type(column.kind),
                            )
                            for column in table.columns
                        ),
                        row_count=len(rows),
                        generation=generation,
                        a1_range=table.a1_range,
                    )
                )
    return tuple(ingested)


def _load(
    conn: Any, name: str, table: TableProfile, rows: Iterable[tuple[Any, ...]]
) -> None:
    """Build the table beside the old one, fill it, then swap it in.

    The swap rather than a truncate-and-fill: it survives a workbook whose columns have
    changed, and it means the old table is only dropped once the new one is known to have
    loaded.
    """
    staging = f"{name}__loading"[:MAX_IDENTIFIER]
    definitions = [
        sql.SQL("{} {}").format(
            sql.Identifier(column.name), sql.SQL(postgres_type(column.kind))
        )
        for column in table.columns
    ]
    definitions += [
        sql.SQL("{} {}").format(sql.Identifier(column), sql.SQL(LINEAGE_TYPES[column]))
        for column in LINEAGE_COLUMNS
    ]

    conn.execute(
        sql.SQL("DROP TABLE IF EXISTS {}").format(
            sql.Identifier(SHEETS_SCHEMA, staging)
        )
    )
    conn.execute(
        sql.SQL("CREATE TABLE {} ({})").format(
            sql.Identifier(SHEETS_SCHEMA, staging), sql.SQL(", ").join(definitions)
        )
    )

    columns = [column.name for column in table.columns] + list(LINEAGE_COLUMNS)
    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(SHEETS_SCHEMA, staging),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join(sql.Placeholder() * len(columns)),
    )
    with conn.cursor() as cursor:
        cursor.executemany(insert, list(rows))

    conn.execute(
        sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(SHEETS_SCHEMA, name))
    )
    conn.execute(
        sql.SQL("ALTER TABLE {} RENAME TO {}").format(
            sql.Identifier(SHEETS_SCHEMA, staging), sql.Identifier(name)
        )
    )
