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


def reconcile_job(job, tally):
    xml, latency, error = tally.export_ledgers(job["company"])
    if xml is None:
        raise RuntimeError(error or "Tally ledger export failed")
    root = ET.fromstring(xml)
    tally_ledgers = {}
    for ledger in root.findall(".//LEDGER"):
        name = ledger.attrib.get("NAME") or ledger.findtext("NAME")
        if name:
            tally_ledgers.setdefault(_norm(name), []).append(name)
    matches, unmatched, ambiguous = [], [], []
    for master in job.get("erp_masters", []):
        candidates = tally_ledgers.get(_norm(master["display_name"]), [])
        if len(candidates) == 1:
            matches.append({**master, "tally_name": candidates[0], "match_type": "Exact Name"})
        elif len(candidates) > 1:
            ambiguous.append({**master, "candidates": candidates})
        else:
            unmatched.append(master)
    summary = {
        "erp_master_count": len(job.get("erp_masters", [])),
        "tally_ledger_count": sum(len(v) for v in tally_ledgers.values()),
        "exact_matches": len(matches),
        "unmatched": len(unmatched),
        "ambiguous": len(ambiguous),
        "latency_ms": round(latency, 1),
    }
    return summary, matches, unmatched + ambiguous


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
                log.info("No reconciliation job and no sync job; sleeping %.1fs", poll)
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
                        log.error("Master probe failed for %s: %s", job["name"], error)
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
