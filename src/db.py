from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from src.models import CanonicalItem


@contextmanager
def get_connection(db_path: Path | str) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS targets (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              platform TEXT NOT NULL CHECK (platform IN ('youtube','tiktok','instagram')),
              handle TEXT NOT NULL,
              external_id TEXT,
              cursor TEXT,
              schedule_minutes INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              last_checked_at TEXT,
              UNIQUE (platform, handle)
            );

            CREATE TABLE IF NOT EXISTS items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              target_id INTEGER NOT NULL REFERENCES targets(id),
              platform TEXT NOT NULL,
              platform_item_id TEXT NOT NULL,
              url TEXT NOT NULL,
              author TEXT,
              published_at TEXT,
              title TEXT,
              text TEXT,
              duration_s INTEGER,
              metrics_json TEXT,
              raw_json TEXT,
              content_hash TEXT,
              fetched_at TEXT NOT NULL,
              processed_at TEXT,
              UNIQUE (platform, platform_item_id)
            );

            CREATE TABLE IF NOT EXISTS metric_snapshots (
              item_id INTEGER NOT NULL REFERENCES items(id),
              captured_at TEXT NOT NULL,
              views INTEGER,
              likes INTEGER,
              comments INTEGER,
              PRIMARY KEY (item_id, captured_at)
            );

            CREATE TABLE IF NOT EXISTS analyses (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id INTEGER NOT NULL REFERENCES items(id),
              model TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              cited_ids_json TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              platform TEXT,
              target_id INTEGER,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              status TEXT NOT NULL,
              items_new INTEGER DEFAULT 0,
              cost_units REAL DEFAULT 0,
              error_class TEXT,
              detail TEXT
            );

            CREATE TABLE IF NOT EXISTS dead_letter (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id INTEGER,
              payload_json TEXT,
              error_class TEXT,
              attempts INTEGER DEFAULT 0,
              next_attempt_at TEXT
            );
            """
        )


def add_target(
    db_path: Path | str,
    platform: str,
    handle: str,
    external_id: str | None = None,
    schedule_minutes: int = 30,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO targets (platform, handle, external_id, schedule_minutes, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?)
            ON CONFLICT(platform, handle) DO UPDATE SET
                status = 'active',
                external_id = COALESCE(excluded.external_id, targets.external_id),
                schedule_minutes = excluded.schedule_minutes
            RETURNING id;
            """,
            (platform, handle, external_id, schedule_minutes, now),
        )
        return int(cursor.fetchone()["id"])


def list_targets(db_path: Path | str) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT t.*, COUNT(i.id) AS items_count
            FROM targets t
            LEFT JOIN items i ON i.target_id = t.id
            GROUP BY t.id
            ORDER BY t.created_at DESC;
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_target(db_path: Path | str, target_id: int) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM targets WHERE id = ?;", (target_id,)).fetchone()
        return dict(row) if row else None


def remove_target(db_path: Path | str, target_id: int) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "UPDATE targets SET status = 'removed' WHERE id = ?;", (target_id,)
        )
        return cursor.rowcount > 0


def update_target_cursor(
    db_path: Path | str, target_id: int, cursor: str, last_checked_at: str
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE targets SET cursor = ?, last_checked_at = ? WHERE id = ?;",
            (cursor, last_checked_at, target_id),
        )


def insert_item(db_path: Path | str, item: CanonicalItem) -> tuple[int, bool]:
    """Insert or update canonical item. Returns (item_id, is_new)."""
    now = datetime.now(timezone.utc).isoformat()
    metrics_str = json.dumps(item.metrics)
    raw_str = json.dumps(item.raw_json)

    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM items WHERE platform = ? AND platform_item_id = ?;",
            (item.platform, item.platform_item_id),
        ).fetchone()

        if existing:
            item_id = existing["id"]
            conn.execute(
                """
                UPDATE items SET
                    metrics_json = ?,
                    duration_s = COALESCE(?, duration_s),
                    title = COALESCE(?, title),
                    text = COALESCE(?, text)
                WHERE id = ?;
                """,
                (metrics_str, item.duration_s, item.title, item.text, item_id),
            )
            is_new = False
        else:
            cursor = conn.execute(
                """
                INSERT INTO items (
                    target_id, platform, platform_item_id, url, author, published_at,
                    title, text, duration_s, metrics_json, raw_json, content_hash, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id;
                """,
                (
                    item.target_id,
                    item.platform,
                    item.platform_item_id,
                    item.url,
                    item.author,
                    item.published_at,
                    item.title,
                    item.text,
                    item.duration_s,
                    metrics_str,
                    raw_str,
                    item.content_hash,
                    now,
                ),
            )
            item_id = cursor.fetchone()["id"]
            is_new = True

        if item.metrics:
            conn.execute(
                """
                INSERT OR REPLACE INTO metric_snapshots (item_id, captured_at, views, likes, comments)
                VALUES (?, ?, ?, ?, ?);
                """,
                (
                    item_id,
                    now,
                    item.metrics.get("views"),
                    item.metrics.get("likes"),
                    item.metrics.get("comments"),
                ),
            )

        return item_id, is_new


def get_unprocessed_items(db_path: Path | str, limit: int = 20) -> list[CanonicalItem]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM items
            WHERE processed_at IS NULL
            ORDER BY datetime(published_at) ASC, id ASC
            LIMIT ?;
            """,
            (limit,),
        ).fetchall()

        items = []
        for r in rows:
            items.append(
                CanonicalItem(
                    id=r["id"],
                    target_id=r["target_id"],
                    platform=r["platform"],
                    platform_item_id=r["platform_item_id"],
                    url=r["url"],
                    author=r["author"] or "",
                    published_at=r["published_at"] or "",
                    title=r["title"],
                    text=r["text"],
                    duration_s=r["duration_s"],
                    metrics=json.loads(r["metrics_json"]) if r["metrics_json"] else {},
                    raw_json=json.loads(r["raw_json"]) if r["raw_json"] else {},
                    content_hash=r["content_hash"] or "",
                )
            )
        return items


def mark_items_processed(db_path: Path | str, item_ids: list[int]) -> None:
    if not item_ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" for _ in item_ids)
    with get_connection(db_path) as conn:
        conn.execute(
            f"UPDATE items SET processed_at = ? WHERE id IN ({placeholders});",
            [now, *item_ids],
        )


def get_item_by_id(db_path: Path | str, item_id: int) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?;", (item_id,)).fetchone()
        return dict(row) if row else None


def check_item_ids_exist(db_path: Path | str, item_ids: list[int]) -> bool:
    if not item_ids:
        return False
    placeholders = ",".join("?" for _ in item_ids)
    with get_connection(db_path) as conn:
        count = conn.execute(
            f"SELECT COUNT(DISTINCT id) AS cnt FROM items WHERE id IN ({placeholders});",
            item_ids,
        ).fetchone()["cnt"]
        return count == len(set(item_ids))


def insert_analysis(
    db_path: Path | str, item_id: int, model: str, payload_json: str, cited_ids: list[str]
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO analyses (item_id, model, payload_json, cited_ids_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id;
            """,
            (item_id, model, payload_json, json.dumps(cited_ids), now),
        )
        return int(cursor.fetchone()["id"])


def start_run(
    db_path: Path | str, kind: str, platform: str | None = None, target_id: int | None = None
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO runs (kind, platform, target_id, started_at, status)
            VALUES (?, ?, ?, ?, 'running')
            RETURNING id;
            """,
            (kind, platform, target_id, now),
        )
        return int(cursor.fetchone()["id"])


def finish_run(
    db_path: Path | str,
    run_id: int,
    status: str,
    items_new: int = 0,
    cost_units: float = 0.0,
    error_class: str | None = None,
    detail: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE runs SET
                finished_at = ?,
                status = ?,
                items_new = ?,
                cost_units = ?,
                error_class = ?,
                detail = ?
            WHERE id = ?;
            """,
            (now, status, items_new, cost_units, error_class, detail, run_id),
        )


def insert_dead_letter(
    db_path: Path | str, run_id: int | None, payload_json: str, error_class: str
) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO dead_letter (run_id, payload_json, error_class, attempts)
            VALUES (?, ?, ?, 1)
            RETURNING id;
            """,
            (run_id, payload_json, error_class),
        )
        return int(cursor.fetchone()["id"])


def purge_expired_raw_json(db_path: Path | str, retention_days: int = 30) -> int:
    # YouTube API compliance: purge raw API payloads older than retention limit
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE items SET raw_json = NULL
            WHERE datetime(fetched_at) < datetime('now', '-{retention_days} days')
              AND raw_json IS NOT NULL;
            """
        )
        return cursor.rowcount


def get_system_status(db_path: Path | str) -> dict[str, Any]:
    with get_connection(db_path) as conn:
        target_count = conn.execute("SELECT COUNT(*) AS c FROM targets WHERE status = 'active';").fetchone()["c"]
        item_count = conn.execute("SELECT COUNT(*) AS c FROM items;").fetchone()["c"]
        unprocessed_count = conn.execute("SELECT COUNT(*) AS c FROM items WHERE processed_at IS NULL;").fetchone()["c"]
        recent_runs = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 3;").fetchall()
        last_error = conn.execute(
            "SELECT * FROM runs WHERE status = 'failed' ORDER BY id DESC LIMIT 1;"
        ).fetchone()
        month_cost = conn.execute(
            """
            SELECT COALESCE(SUM(cost_units), 0.0) AS total_cost
            FROM runs
            WHERE strftime('%Y-%m', started_at) = strftime('%Y-%m', 'now');
            """
        ).fetchone()["total_cost"]

        return {
            "active_targets": target_count,
            "total_items": item_count,
            "queue_depth": unprocessed_count,
            "recent_runs": [dict(r) for r in recent_runs],
            "last_error": dict(last_error) if last_error else None,
            "month_cost_units": float(month_cost),
        }
