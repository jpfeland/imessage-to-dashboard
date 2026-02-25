"""
Utility functions for phone normalization and Apple timestamp conversion.
"""

import re
from datetime import datetime, timezone

# Apple's CoreData timestamp epoch starts at 2001-01-01 00:00:00 UTC.
# Timestamps in modern macOS chat.db are stored in nanoseconds.
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
APPLE_EPOCH_UNIX = APPLE_EPOCH.timestamp()


def normalize_phone_key(raw: str) -> str | None:
    """
    Normalize a raw phone string to a 10-digit key for matching.

    Rules:
      - Strip all non-digit characters.
      - If the result has >= 10 digits, use the LAST 10 digits.
      - If fewer than 10 digits remain, return None.

    Examples:
      "+1 (612) 555-1234"  -> "6125551234"
      "16125551234"        -> "6125551234"
      "abc"                -> None
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 10:
        return digits[-10:]
    return None


def apple_ts_to_iso(ts: int) -> str:
    """
    Convert an Apple CoreData timestamp (nanoseconds since 2001-01-01 UTC)
    to an ISO 8601 UTC string.

    Apple changed from seconds to nanoseconds in macOS 10.13 (High Sierra).
    We detect which unit by checking whether the raw value is large enough
    to be nanoseconds: if ts > 1e10 we treat it as nanoseconds, otherwise
    as seconds.  This keeps the function correct against older rows that
    were written before the format change.
    """
    if ts is None:
        # Fallback: use current time so the row is still insertable.
        return datetime.now(tz=timezone.utc).isoformat()

    if ts > 1_000_000_000_0:  # 10-digit threshold → nanoseconds
        seconds = ts / 1_000_000_000
    else:
        seconds = float(ts)

    dt = datetime.fromtimestamp(APPLE_EPOCH_UNIX + seconds, tz=timezone.utc)
    return dt.isoformat()
