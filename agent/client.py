from __future__ import annotations

import requests


class FrappeClient:
    def __init__(self, base_url, api_key, api_secret, verify_tls=True, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"token {api_key}:{api_secret}", "Content-Type": "application/json"}
        self.verify_tls = verify_tls
        self.timeout = timeout

    def pull_job(self, agent_id):
        r = requests.post(
            f"{self.base_url}/api/method/tally_bridge.api.pull_next_job",
            json={"agent_id": agent_id}, headers=self.headers, timeout=self.timeout, verify=self.verify_tls,
        )
        if not r.ok:
            raise RuntimeError(
                f"Frappe API {r.status_code}: {r.text[:5000]}"
            )
        return r.json()["message"]["job"]

    def ack(self, queue_name, success, tally_response="", http_status=0, latency_ms=0, error=None):
        body = {
            "queue_name": queue_name,
            "success": 1 if success else 0,
            "tally_response": tally_response,
            "http_status": http_status,
            "latency_ms": latency_ms,
            "error": error,
        }
        r = requests.post(
            f"{self.base_url}/api/method/tally_bridge.api.ack_job",
            json=body, headers=self.headers, timeout=self.timeout, verify=self.verify_tls,
        )
        r.raise_for_status()
        return r.json()


    def pull_reconciliation(self, agent_id):
        r = requests.post(
            f"{self.base_url}/api/method/tally_bridge.api.pull_reconciliation",
            headers=self._headers(),
            json={"agent_id": agent_id},
            timeout=self.timeout,
        )
        if not r.ok:
            raise RuntimeError(f"Frappe API {r.status_code}: {r.text[:5000]}")
        return r.json().get("message", {}).get("job")

    def ack_reconciliation(self, job_name, success, summary_json, matches_json, unmatched_json, error=""):
        r = requests.post(
            f"{self.base_url}/api/method/tally_bridge.api.ack_reconciliation",
            headers=self._headers(),
            json={
                "job_name": job_name,
                "success": int(success),
                "summary_json": summary_json,
                "matches_json": matches_json,
                "unmatched_json": unmatched_json,
                "error": error,
            },
            timeout=self.timeout,
        )
        if not r.ok:
            raise RuntimeError(f"Frappe API {r.status_code}: {r.text[:5000]}")
        return r.json().get("message", {})
