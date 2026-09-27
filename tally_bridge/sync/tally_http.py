from __future__ import annotations

import time
import requests


class TallyHttpError(RuntimeError):
    pass


class TallyHttpClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def post_xml(self, xml_payload: str) -> tuple[int, str, float]:
        start = time.perf_counter()
        response = requests.post(
            self.base_url,
            data=xml_payload.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=self.timeout,
        )
        elapsed = time.perf_counter() - start
        return response.status_code, response.text, elapsed
