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


    def master_exists(self, name, object_type="Ledger", company=None):
        """Return (exists, latency_ms, response_xml). exists=None means the probe failed."""
        company_tag = f"<SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>" if company else ""
        payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST>
    <TYPE>OBJECT</TYPE>
    <SUBTYPE>{object_type}</SUBTYPE>
    <ID TYPE="Name">{name}</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$SysName:XML</SVEXPORTFORMAT>
        {company_tag}
      </STATICVARIABLES>
      <FETCHLIST>
        <FETCH>Name</FETCH>
      </FETCHLIST>
    </DESC>
  </BODY>
</ENVELOPE>"""
        start = time.perf_counter()
        try:
            response = requests.post(
                self.base_url,
                data=payload.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                timeout=self.timeout,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return None, elapsed, str(exc)

        elapsed = (time.perf_counter() - start) * 1000
        if response.status_code != 200:
            return None, elapsed, response.text

        from xml.etree import ElementTree as ET
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            return None, elapsed, response.text

        tag = object_type.upper()
        if root.find(f".//{tag}") is not None:
            return True, elapsed, response.text

        return False, elapsed, response.text
