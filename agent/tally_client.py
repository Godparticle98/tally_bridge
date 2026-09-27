from __future__ import annotations

import time
import requests


class TallyClient:
    def __init__(self, base_url, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def send_xml(self, payload):
        start = time.perf_counter()
        r = requests.post(
            self.base_url,
            data=payload.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=self.timeout,
        )
        elapsed = (time.perf_counter() - start) * 1000
        return r.status_code, r.text, elapsed
