"""
Supabase REST (PostgREST) client — read users, write communications.

All HTTP is done via the standard `requests` library.  No Supabase SDK.
"""

import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500  # max rows per Supabase insert request


def _get_env() -> tuple[str, str]:
    """Return (supabase_url, service_key).  Fails fast if either is missing."""
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    missing = []
    if not url:
        missing.append("SUPABASE_URL")
    if not key:
        missing.append("SUPABASE_SERVICE_KEY")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}"
        )
    return url, key


def _headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Reading users
# ---------------------------------------------------------------------------

def fetch_allowed_users() -> dict[str, str]:
    """
    Query Supabase for all users that have a non-null phone_number.

    Returns:
        { phone_key (10 digits): user_id (uuid string) }
    """
    from imessage_ingest.utils import normalize_phone_key

    url, key = _get_env()
    endpoint = f"{url}/rest/v1/users"
    params = {"select": "id,phone_number", "phone_number": "not.is.null"}

    resp = requests.get(endpoint, headers=_headers(key), params=params, timeout=30)
    if not resp.ok:
        raise RuntimeError(
            f"Failed to fetch users from Supabase: {resp.status_code} {resp.text}"
        )

    rows = resp.json()
    allowed: dict[str, str] = {}
    for row in rows:
        key_val = normalize_phone_key(row.get("phone_number") or "")
        if key_val:
            allowed[key_val] = row["id"]
        else:
            logger.warning(
                "User %s has an un-normalizable phone_number '%s'; skipping.",
                row.get("id"),
                row.get("phone_number"),
            )
    logger.info("Fetched %d allowed users from Supabase.", len(allowed))
    return allowed


def fetch_user_by_id(user_id: str) -> dict:
    """
    Fetch a single user row by UUID.  Fails if not found or phone_number is null.
    """
    url, key = _get_env()
    endpoint = f"{url}/rest/v1/users"
    params = {"select": "id,phone_number", "id": f"eq.{user_id}"}

    resp = requests.get(endpoint, headers=_headers(key), params=params, timeout=30)
    if not resp.ok:
        raise RuntimeError(
            f"Failed to fetch user {user_id}: {resp.status_code} {resp.text}"
        )

    rows = resp.json()
    if not rows:
        raise ValueError(f"No user found in Supabase with id={user_id}")

    user = rows[0]
    if not user.get("phone_number"):
        raise ValueError(
            f"User {user_id} exists but has no phone_number set in Supabase."
        )
    return user


# ---------------------------------------------------------------------------
# Writing communications
# ---------------------------------------------------------------------------

def insert_communications(records: list[dict]) -> None:
    """
    Upsert a list of communication records into Supabase in chunks of CHUNK_SIZE.

    Uses `Prefer: resolution=ignore-duplicates` so re-runs are safe.
    Raises on any non-2xx response.
    """
    if not records:
        logger.info("No records to insert.")
        return

    url, key = _get_env()
    endpoint = f"{url}/rest/v1/communications"
    hdrs = {
        **_headers(key),
        "Prefer": "resolution=ignore-duplicates",
    }

    total_uploaded = 0
    for i in range(0, len(records), CHUNK_SIZE):
        chunk = records[i : i + CHUNK_SIZE]
        resp = requests.post(
            endpoint,
            headers=hdrs,
            data=json.dumps(chunk),
            timeout=60,
        )
        if not resp.ok:
            raise RuntimeError(
                f"Supabase insert failed (chunk {i // CHUNK_SIZE + 1}): "
                f"{resp.status_code} {resp.text}"
            )
        total_uploaded += len(chunk)
        logger.debug("Uploaded chunk of %d records (total so far: %d).", len(chunk), total_uploaded)

    logger.info("Uploaded %d communication record(s) to Supabase.", total_uploaded)
