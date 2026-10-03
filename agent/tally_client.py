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

        tag = object_type.upper().replace(" ", "")
        return root.find(f".//{tag}") is not None, elapsed, response.text

    def export_collection(self, collection_name, object_type, native_methods, company=None):
        """Export one complete Tally master collection using inline TDL."""
        company_tag = f"<SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>" if company else ""
        methods = "".join(f"<NATIVEMETHOD>{method}</NATIVEMETHOD>" for method in native_methods)
        payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST>
    <TYPE>COLLECTION</TYPE>
    <ID>{collection_name}</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {company_tag}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="{collection_name}" ISMODIFY="No" ISINITIALIZE="Yes">
            <TYPE>{object_type}</TYPE>
            {methods}
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
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
            return None, (time.perf_counter() - start) * 1000, str(exc)
        elapsed = (time.perf_counter() - start) * 1000
        if response.status_code != 200:
            return None, elapsed, response.text
        return response.text, elapsed, None

    def export_ledgers(self, company=None):
        return self.export_collection(
            "TallyBridgeLedgerCollection",
            "Ledger",
            ["Name", "Parent", "IncomeTaxNumber", "LedgerPhone", "LedgerMobile",
             "LedgerContact", "StateName", "PINCode", "MailingName"],
            company,
        )

    def export_stock_items(self, company=None):
        return self.export_collection(
            "TallyBridgeStockItemCollection",
            "Stock Item",
            ["Name", "Parent", "BaseUnits", "OpeningBalance", "OpeningRate"],
            company,
        )

    def export_units(self, company=None):
        return self.export_collection(
            "TallyBridgeUnitCollection",
            "Unit",
            ["Name", "BaseUnits", "Conversion"],
            company,
        )
