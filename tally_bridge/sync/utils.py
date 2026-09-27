from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal


def json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def canonical_json(value) -> str:
    return json.dumps(value, default=json_default, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_remote_id(site: str, doctype: str, name: str) -> str:
    # UUIDv5 gives a stable identifier for the same ERPNext document across retries/export jobs.
    seed = f"{site}|{doctype}|{name}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def xml_escape_text(value) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
