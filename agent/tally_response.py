from __future__ import annotations

from xml.etree import ElementTree as ET


def parse_tally_response(xml_text: str) -> dict:
    """Normalize common Tally XML response shapes into one small structure."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return {"ok": False, "errors": 1, "message": f"Invalid XML response: {exc}", "created": 0, "altered": 0, "cancelled": 0}

    header_status = root.findtext("./HEADER/STATUS")
    errors_text = root.findtext(".//ERRORS") or "0"
    created_text = root.findtext(".//CREATED") or "0"
    altered_text = root.findtext(".//ALTERED") or "0"
    cancelled_text = root.findtext(".//CANCELLED") or "0"

    try:
        errors = int(float(errors_text))
    except ValueError:
        errors = 1
    try:
        created = int(float(created_text))
    except ValueError:
        created = 0
    try:
        altered = int(float(altered_text))
    except ValueError:
        altered = 0
    try:
        cancelled = int(float(cancelled_text))
    except ValueError:
        cancelled = 0

    messages = []
    for path in [".//LINEERROR", ".//DESC", ".//MESSAGE"]:
        for node in root.findall(path):
            if node.text and node.text.strip():
                messages.append(node.text.strip())

    ok = errors == 0 and header_status not in {"0", "False"}
    return {
        "ok": ok,
        "header_status": header_status,
        "errors": errors,
        "created": created,
        "altered": altered,
        "cancelled": cancelled,
        "message": " | ".join(dict.fromkeys(messages)),
    }
