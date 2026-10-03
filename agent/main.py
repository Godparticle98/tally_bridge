from __future__ import annotations

import json
import logging
import time
from xml.etree import ElementTree as ET
from pathlib import Path

import yaml

from client import FrappeClient
from tally_client import TallyClient
from tally_response import parse_tally_response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tally-bridge-agent")


def _norm(value):
    return " ".join(str(value or "").strip().casefold().split())


def _sanitize_xml_for_parser(xml):
    import re

    xml = re.sub(
        r"&#(?:x[0-8bBcCdDeEfF]+|[0-9]+);",
        lambda m: "" if not _valid_xml_codepoint(m.group(0)) else m.group(0),
        xml,
        flags=re.IGNORECASE,
    )
    return "".join(
        ch for ch in xml
        if ch in "\t\n\r"
        or 0x20 <= ord(ch) <= 0xD7FF
        or 0xE000 <= ord(ch) <= 0xFFFD
        or 0x10000 <= ord(ch) <= 0x10FFFF
    )


def _valid_xml_codepoint(ref):
    try:
        value = int(ref[3:-1], 16) if ref.lower().startswith("&#x") else int(ref[2:-1])
    except ValueError:
        return False
    return (
        value in (0x9, 0xA, 0xD)
        or 0x20 <= value <= 0xD7FF
        or 0xE000 <= value <= 0xFFFD
        or 0x10000 <= value <= 0x10FFFF
    )


def _parse_collection(xml, element_names):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as first_error:
        log.warning("Tally collection export contains XML 1.0-invalid characters; sanitizing: %s", first_error)
        root = ET.fromstring(_sanitize_xml_for_parser(xml))

    wanted = {name.upper() for name in element_names}
    result = {}
    for element in root.iter():
        if element.tag.upper() not in wanted:
            continue
        name = element.attrib.get("NAME") or element.findtext("NAME")
        if name:
            result.setdefault(_norm(name), []).append(name)
    return result


def _match_collection(erp_masters, tally_objects):
    matches, unmatched, ambiguous = [], [], []
    for master in erp_masters:
        candidates = tally_objects.get(_norm(master["display_name"]), [])
        if len(candidates) == 1:
            matches.append({
                **master,
                "tally_name": candidates[0],
                "match_type": "Exact Name",
            })
        elif len(candidates) > 1:
            ambiguous.append({
                **master,
                "match_status": "Needs Review",
                "candidates": candidates,
            })
        else:
            unmatched.append({
                **master,
                "match_status": "Create Required",
            })

    buckets = {}
    for match in matches:
        key = (match["object_type"], _norm(match["tally_name"]))
        buckets.setdefault(key, []).append(match)

    for bucket in buckets.values():
        status = "Shared Mapping" if len(bucket) > 1 else "Matched"
        for match in bucket:
            match["reconciliation_status"] = status

    return matches, unmatched, ambiguous


def reconcile_job(job, tally):
    """Reconcile each ERPNext master against its Tally object namespace."""
    by_type = {}
    for master in job.get("erp_masters", []):
        by_type.setdefault(master["object_type"], []).append(master)

    definitions = {
        "Ledger": (tally.export_ledgers, ["LEDGER"]),
        "Stock Item": (tally.export_stock_items, ["STOCKITEM"]),
        "Unit": (tally.export_units, ["UNIT"]),
    }

    all_matches, all_unmatched, all_ambiguous = [], [], []
    type_summary = {}
    total_latency = 0.0

    for object_type, masters in by_type.items():
        exporter, element_names = definitions.get(object_type, (None, None))
        if exporter is None:
            all_unmatched.extend(masters)
            continue

        xml, latency, error = exporter(job["company"])
        total_latency += latency
        if xml is None:
            raise RuntimeError(f"Tally {object_type} export failed: {error or 'unknown error'}")

        tally_objects = _parse_collection(xml, element_names)
        matches, unmatched, ambiguous = _match_collection(masters, tally_objects)
        all_matches.extend(matches)
        all_unmatched.extend(unmatched)
        all_ambiguous.extend(ambiguous)

        shared_records = sum(
            1 for item in matches if item.get("reconciliation_status") == "Shared Mapping"
        )
        shared_buckets = {
            (_norm(item["object_type"]), _norm(item["tally_name"]))
            for item in matches
            if item.get("reconciliation_status") == "Shared Mapping"
        }
        type_summary[object_type] = {
            "erp_count": len(masters),
            "tally_count": sum(len(v) for v in tally_objects.values()),
            "exact_matches": len(matches),
            "matched": sum(1 for item in matches if item.get("reconciliation_status") == "Matched"),
            "shared_mappings": len(shared_buckets),
            "shared_mapping_records": shared_records,
            "create_required": len(unmatched),
            "needs_review": len(ambiguous),
            "unmatched": len(unmatched),
            "ambiguous": len(ambiguous),
            "latency_ms": round(latency, 1),
        }

    matched_count = sum(1 for item in all_matches if item.get("reconciliation_status") == "Matched")
    shared_record_count = sum(
        1 for item in all_matches if item.get("reconciliation_status") == "Shared Mapping"
    )
    create_required_count = sum(1 for item in all_unmatched if item.get("match_status") == "Create Required")
    needs_review_count = len(all_ambiguous)

    shared_buckets = {}
    for item in all_matches:
        if item.get("reconciliation_status") == "Shared Mapping":
            key = (item["object_type"], _norm(item["tally_name"]))
            shared_buckets[key] = True

    summary = {
        "erp_master_count": len(job.get("erp_masters", [])),
        "exact_matches": len(all_matches),
        "matched": matched_count,
        "shared_mappings": len(shared_buckets),
        "shared_mapping_records": shared_record_count,
        "create_required": create_required_count,
        "needs_review": needs_review_count,
        "unmatched": create_required_count,
        "ambiguous": needs_review_count,
        "many_to_one_erp_mappings": len(shared_buckets),
        "latency_ms": round(total_latency, 1),
        "by_object_type": type_summary,
    }
    return summary, all_matches, all_unmatched + all_ambiguous


def load_config(path="config.yaml"):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def main():
    cfg = load_config()
    frappe = FrappeClient(
        cfg["frappe_base_url"], cfg["frappe_api_key"], cfg["frappe_api_secret"],
        verify_tls=cfg.get("verify_tls", True), timeout=cfg.get("request_timeout_seconds", 30)
    )
    tally = TallyClient(cfg.get("tally_url", "http://127.0.0.1:9000"), timeout=cfg.get("request_timeout_seconds", 30))
    poll = float(cfg.get("poll_interval_seconds", 2))
    agent_id = cfg["agent_id"]

    log.info("Tally Bridge Agent started: %s", agent_id)
    log.info("Frappe endpoint: %s", cfg["frappe_base_url"])
    log.info("Tally endpoint: %s", cfg.get("tally_url", "http://127.0.0.1:9000"))
    log.info("Poll interval: %.1fs", poll)
    while True:
        try:
            reconciliation = frappe.pull_reconciliation(agent_id)
            if reconciliation:
                log.info("Reconciliation job received: %s", reconciliation.get("name"))
                try:
                    summary, matches, unmatched = reconcile_job(reconciliation, tally)
                    frappe.ack_reconciliation(
                        reconciliation["name"], True,
                        json.dumps(summary), json.dumps(matches), json.dumps(unmatched)
                    )
                    log.info(
                        "Reconciliation %s completed: %s exact, %s unmatched, %s ambiguous",
                        reconciliation["name"], summary["exact_matches"],
                        summary["unmatched"], summary["ambiguous"],
                    )
                except Exception as exc:
                    log.exception("Reconciliation failed")
                    frappe.ack_reconciliation(
                        reconciliation["name"], False, "{}", "[]", "[]", str(exc)
                    )
                continue

            job = frappe.pull_job(agent_id)
            if not job:
                time.sleep(poll)
                continue

            log.info("Processing %s %s/%s (%s)", job["name"], job["source_doctype"], job["source_name"], job["event"])
            try:
                probe_latency = 0.0
                payload = job["payload_xml"]
                probe = job.get("master_probe")
                if probe:
                    exists, probe_latency, probe_response = tally.master_exists(
                        probe["name"], probe["object_type"], probe.get("company")
                    )
                    if exists is None:
                        error = f"Tally master existence probe failed: {probe_response[:2000]}"
                        frappe.ack(job["name"], False, probe_response, 0, probe_latency, error)
                        continue
                    if not exists:
                        payload = job.get("fallback_payload_xml") or payload
                        log.info(
                            "Master %s/%s is not in Tally; using CREATE instead of legacy ALTER",
                            job["source_doctype"], probe["name"],
                        )

                status, response, latency = tally.send_xml(payload)
                total_latency = probe_latency + latency
                result = parse_tally_response(response)
                success = status == 200 and result["ok"]
                error = None if success else (result.get("message") or response[:2000])
                frappe.ack(job["name"], success, response, status, total_latency, error)
                if success:
                    log.info("Tally accepted %s in %.1f ms", job["name"], total_latency)
                else:
                    log.error("Tally rejected %s: %s", job["name"], error)
            except Exception as exc:
                log.exception("Tally call failed")
                frappe.ack(job["name"], False, "", 0, 0, str(exc))
        except Exception as exc:
            log.exception("Agent loop failure: %s", exc)
            time.sleep(min(poll * 5, 15))


if __name__ == "__main__":
    main()
