#!/usr/bin/env python3
"""
iMessage → Supabase ingestion service.

Usage:
    imessage-to-dashboard live
    imessage-to-dashboard backfill --user-id <uuid>
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from imessage_ingest import db, supabase
from imessage_ingest.utils import apple_ts_to_iso, normalize_phone_key

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State persistence (live mode only)
# ---------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / "state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state.json (%s); starting from 0.", exc)
    return {"last_seen_message_id": 0}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))
    logger.debug("State saved: %s", state)


# ---------------------------------------------------------------------------
# Record builder
# ---------------------------------------------------------------------------

def build_record(row, client_id: str) -> dict:
    """Convert a chat.db row into a Supabase communications dict."""
    direction = "outgoing" if row["is_from_me"] else "incoming"
    return {
        "source": "imessage",
        "external_id": str(row["rowid"]),
        "client_id": client_id,
        "thread_key": str(row["chat_id"]) if row["chat_id"] is not None else None,
        "direction": direction,
        "sender_handle": row["handle"] or "",
        "body": row["text"],
        "sent_at": apple_ts_to_iso(row["date"]),
    }


# ---------------------------------------------------------------------------
# LIVE MODE
# ---------------------------------------------------------------------------

def run_live() -> None:
    logger.info("=== MODE: LIVE ===")

    state = load_state()
    last_id: int = state.get("last_seen_message_id", 0)
    logger.info("Resuming from last_seen_message_id=%d", last_id)

    # Fetch allowed users before touching the DB copy.
    allowed = supabase.fetch_allowed_users()

    # Copy and open chat.db.
    db.copy_chat_db()
    conn = db.open_copy()

    try:
        rows = db.fetch_messages_since(conn, last_id)
    except Exception as exc:
        conn.close()
        raise RuntimeError(f"SQLite query failed: {exc}") from exc
    finally:
        conn.close()

    total_scanned = len(rows)
    logger.info("Messages scanned: %d", total_scanned)

    records: list[dict] = []
    new_last_id = last_id

    for row in rows:
        # Always advance the high-water mark, even for skipped messages.
        if row["rowid"] > new_last_id:
            new_last_id = row["rowid"]

        phone_key = normalize_phone_key(row["handle"] or "")
        if phone_key is None or phone_key not in allowed:
            continue

        records.append(build_record(row, allowed[phone_key]))

    matched = len(records)
    logger.info("Messages matched (whitelisted): %d", matched)

    if records:
        supabase.insert_communications(records)

    logger.info("Messages uploaded: %d", matched)
    logger.info("New last_seen_message_id: %d", new_last_id)

    state["last_seen_message_id"] = new_last_id
    save_state(state)


# ---------------------------------------------------------------------------
# BACKFILL MODE
# ---------------------------------------------------------------------------

def run_backfill(user_id: str) -> None:
    logger.info("=== MODE: BACKFILL | user_id=%s ===", user_id)

    # Fetch and validate user.
    user = supabase.fetch_user_by_id(user_id)
    phone_number = user["phone_number"]
    target_key = normalize_phone_key(phone_number)
    if target_key is None:
        raise ValueError(
            f"User {user_id} has phone_number '{phone_number}' which cannot be "
            "normalized to a 10-digit key."
        )
    logger.info("Targeting phone_number=%s  (key=%s)", phone_number, target_key)

    # Copy and open chat.db.
    db.copy_chat_db()
    conn = db.open_copy()

    try:
        all_handles = db.fetch_all_handles(conn)
        matching_handle_ids = [
            h["ROWID"]
            for h in all_handles
            if normalize_phone_key(h["id"] or "") == target_key
        ]

        if not matching_handle_ids:
            logger.warning(
                "No handle rows found in chat.db matching key=%s — "
                "no messages to backfill.",
                target_key,
            )
            conn.close()
            return

        logger.info(
            "Found %d handle row(s) matching key=%s: %s",
            len(matching_handle_ids),
            target_key,
            matching_handle_ids,
        )

        rows = db.fetch_messages_for_handles(conn, matching_handle_ids)
    except Exception as exc:
        conn.close()
        raise RuntimeError(f"SQLite query failed: {exc}") from exc
    finally:
        conn.close()

    total_scanned = len(rows)
    logger.info("Messages scanned: %d", total_scanned)

    records = [build_record(row, user_id) for row in rows]
    matched = len(records)
    logger.info("Messages matched: %d (all belong to this user)", matched)

    if records:
        supabase.insert_communications(records)

    logger.info("Messages uploaded: %d (idempotent — duplicates ignored)", matched)
    # DO NOT modify state.json in backfill mode.


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="iMessage → Supabase ingestion service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  imessage-to-dashboard live\n"
            "  imessage-to-dashboard backfill --user-id 550e8400-e29b-41d4-a716-446655440000\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("live", help="Incremental sync from last seen message ID.")

    backfill_parser = sub.add_parser(
        "backfill", help="Full historical sync for one user."
    )
    backfill_parser.add_argument(
        "--user-id", required=True, metavar="UUID",
        help="Supabase users.id for the client to backfill."
    )

    args = parser.parse_args()

    try:
        if args.command == "live":
            run_live()
        elif args.command == "backfill":
            run_backfill(args.user_id)
    except (EnvironmentError, ValueError, RuntimeError, FileNotFoundError) as exc:
        logger.error("Fatal: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
