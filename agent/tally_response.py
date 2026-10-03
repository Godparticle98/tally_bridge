from __future__ import annotations

from xml.etree import ElementTree as ET


def _int(root, path: str) -> int:
    try:
        return int(float(root.findtext(path) or "0"))
    except (TypeError, ValueError):
        return 0


def parse_tally_response(xml_text: str) -> dict:
    """Normalize Tally import responses and require an actual processed object."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return {
            "ok": False,
            "errors": 1,
            "exceptions": 0,
            "created": 0,
            "altered": 0,
            "cancelled": 0,
            "ignored": 0,
            "combined": 0,
            "message": f"Invalid XML response: {exc}",
        }

    header_status = root.findtext("./HEADER/STATUS")
    created = _int(root, ".//CREATED")
    altered = _int(root, ".//ALTERED")
    cancelled = _int(root, ".//CANCELLED")
    ignored = _int(root, ".//IGNORED")
    combined = _int(root, ".//COMBINED")
    errors = _int(root, ".//ERRORS")
    exceptions = _int(root, ".//EXCEPTIONS")

    messages = []
    for path in (".//LINEERROR", ".//DESC", ".//MESSAGE", ".//ERROR", ".//EXCEPTION"):
        for node in root.findall(path):
            if node.text and node.text.strip():
                messages.append(node.text.strip())

    # Tally documents the import result using CREATED/ALTERED/ERRORS and
    # reports STATUS=0 for failed requests. HTTP 200 alone is not success.
    processed = created + altered + cancelled + combined
    ok = (
        errors == 0
        and exceptions == 0
        and header_status not in {"0", "False"}
        and processed > 0
    )

    if errors or exceptions:
        messages.append(f"Tally import reported errors={errors}, exceptions={exceptions}")
    elif processed == 0:
        messages.append("Tally returned no created/altered/cancelled objects")

    return {
        "ok": ok,
        "header_status": header_status,
        "errors": errors,
        "exceptions": exceptions,
        "created": created,
        "altered": altered,
        "cancelled": cancelled,
        "ignored": ignored,
        "combined": combined,
        "message": " | ".join(dict.fromkeys(messages)),
    }
