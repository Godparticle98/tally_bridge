"""
tally_bridge/utils/tally_connector.py

Connects to Tally Prime's built-in XML server (default port 9000).
Tally must be open with a company loaded and XML server enabled in
Gateway of Tally → F12 Configuration → Advanced Configuration → Enable XML Server.
"""

import frappe
import requests
from frappe.utils import now_datetime


class TallyConnector:
    """HTTP client that speaks Tally Prime's XML protocol."""

    def __init__(self):
        settings = frappe.get_single("Tally Settings")
        self.host = settings.tally_host or "localhost"
        self.port = settings.tally_port or 9000
        self.company = settings.company_name_in_tally
        self.base_url = f"http://{self.host}:{self.port}"
        self.timeout = 60  # seconds

    # ──────────────────────────────────────────────────────────────
    # Core HTTP method
    # ──────────────────────────────────────────────────────────────

    def _post_xml(self, xml_string):
        """POST raw XML to Tally and return (success, response_text)."""
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
        }
        try:
            resp = requests.post(
                self.base_url,
                data=xml_string.encode("utf-8"),
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True, resp.text
        except requests.exceptions.ConnectionError:
            return False, (
                f"Cannot connect to Tally at {self.base_url}. "
                "Make sure Tally Prime is open and XML Server is enabled."
            )
        except requests.exceptions.Timeout:
            return False, f"Tally connection timed out after {self.timeout}s."
        except requests.exceptions.HTTPError as e:
            return False, f"HTTP error from Tally: {e}"
        except Exception as e:
            return False, str(e)

    # ──────────────────────────────────────────────────────────────
    # Connectivity test
    # ──────────────────────────────────────────────────────────────

    def test_connection(self):
        """Send a simple company info request to verify Tally is reachable."""
        xml = f"""<ENVELOPE>
<HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>
<BODY>
  <EXPORTDATA>
    <REQUESTDESC>
      <REPORTNAME>List of Companies</REPORTNAME>
    </REQUESTDESC>
  </EXPORTDATA>
</BODY>
</ENVELOPE>"""
        success, response = self._post_xml(xml)
        return success, response

    # ──────────────────────────────────────────────────────────────
    # Import XML data into Tally
    # ──────────────────────────────────────────────────────────────

    def push_xml(self, xml_string):
        """Push a TALLYMESSAGE XML string directly to Tally Prime."""
        return self._post_xml(xml_string)

    # ──────────────────────────────────────────────────────────────
    # Parse Tally response
    # ──────────────────────────────────────────────────────────────

    def parse_tally_response(self, response_text):
        """
        Extract created/altered/errors from Tally's response XML.
        Tally returns LINEERROR elements for failures.
        """
        result = {
            "created": 0,
            "altered": 0,
            "errors": [],
            "raw": response_text
        }
        try:
            from lxml import etree
            root = etree.fromstring(response_text.encode("utf-8"))
            for elem in root.iter("CREATED"):
                result["created"] += int(elem.text or 0)
            for elem in root.iter("ALTERED"):
                result["altered"] += int(elem.text or 0)
            for elem in root.iter("LINEERROR"):
                result["errors"].append(elem.text or "")
        except Exception:
            pass
        return result


# ──────────────────────────────────────────────────────────────────────────────
# Standalone helper used by the API layer
# ──────────────────────────────────────────────────────────────────────────────

def push_to_tally(xml_string, export_log_name=None):
    """
    Push XML to Tally and update the Export Log record.
    Returns dict with keys: success, created, altered, errors, response.
    """
    conn = TallyConnector()
    success, response = conn.push_xml(xml_string)

    result = {
        "success": success,
        "response": response,
        "created": 0,
        "altered": 0,
        "errors": [],
    }

    if success:
        parsed = conn.parse_tally_response(response)
        result.update({
            "created": parsed["created"],
            "altered": parsed["altered"],
            "errors": parsed["errors"],
        })

    if export_log_name:
        try:
            log = frappe.get_doc("Tally Export Log", export_log_name)
            log.tally_response = response[:2000] if response else ""
            if success and not result["errors"]:
                log.status = "Success"
            elif success and result["errors"]:
                log.status = "Partial"
                log.error_log = "\n".join(result["errors"])
            else:
                log.status = "Failed"
                log.error_log = response
            log.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Tally Bridge: Export Log update failed")

    return result
