"""Parser for the ``X-Simulate-Failure`` test hook.

Format: comma-separated ``<key_id>=<status>`` pairs. ``*`` targets every key.
Only 429, 500, 502 and 503 are accepted; anything else is ignored so a typo in
the header can never take the endpoint down.
"""

from __future__ import annotations

from typing import Dict, Optional

ALLOWED_STATUSES = {429, 500, 502, 503}
WILDCARD = "*"


def parse_simulate_header(raw: Optional[str]) -> Dict[str, int]:
    """Return a mapping of ``key_id`` (or ``*``) to the status to simulate.

    An absent, blank or fully malformed header yields an empty mapping, which
    means "simulate nothing".
    """
    if not raw:
        return {}

    directives: Dict[str, int] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key_id, _, status_text = pair.partition("=")
        key_id = key_id.strip()
        status_text = status_text.strip()
        if not key_id or not status_text.isdigit():
            continue
        status = int(status_text)
        if status not in ALLOWED_STATUSES:
            continue
        directives[key_id] = status
    return directives


def simulated_status_for(directives: Dict[str, int], key_id: str) -> Optional[int]:
    """Status this key should pretend to return, or ``None`` to call for real.

    An explicit key id wins over the wildcard, so ``*=500,key_2=429`` is
    expressible even though the assignment does not require it.
    """
    if not directives:
        return None
    if key_id in directives:
        return directives[key_id]
    return directives.get(WILDCARD)
