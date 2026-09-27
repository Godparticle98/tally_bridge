from __future__ import annotations

import logging
import time
from pathlib import Path

import yaml

from client import FrappeClient
from tally_client import TallyClient
from tally_response import parse_tally_response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tally-bridge-agent")


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
    while True:
        try:
            job = frappe.pull_job(agent_id)
            if not job:
                time.sleep(poll)
                continue

            log.info("Processing %s %s/%s (%s)", job["name"], job["source_doctype"], job["source_name"], job["event"])
            try:
                status, response, latency = tally.send_xml(job["payload_xml"])
                result = parse_tally_response(response)
                success = status == 200 and result["ok"]
                error = None if success else (result.get("message") or response[:2000])
                frappe.ack(job["name"], success, response, status, latency, error)
                if success:
                    log.info("Tally accepted %s in %.1f ms", job["name"], latency)
                else:
                    log.error("Tally rejected %s: %s", job["name"], error)
            except Exception as exc:
                log.exception("Tally call failed")
                frappe.ack(job["name"], False, "", 0, 0, str(exc))
        except Exception:
            log.exception("Agent loop failure")
            time.sleep(min(poll * 5, 15))


if __name__ == "__main__":
    main()
