# Tally Bridge — ERPNext to Tally Prime Integration

A Frappe/ERPNext v16 custom app that exports accounting data to Tally Prime 4.x via native XML format, with optional direct push through Tally's built-in HTTP XML server.

---

## What it exports

| ERPNext Doctype       | Tally Entity                  | Voucher Type         |
|-----------------------|-------------------------------|----------------------|
| Account (GL)          | Ledger (under mapped group)   | Master               |
| Customer              | Ledger under Sundry Debtors   | Master               |
| Supplier              | Ledger under Sundry Creditors | Master               |
| Sales Invoice         | Sales Voucher                 | Sales                |
| Purchase Invoice      | Purchase Voucher              | Purchase             |
| Payment Entry (Rcv)   | Receipt Voucher               | Receipt              |
| Payment Entry (Pay)   | Payment Voucher               | Payment              |
| Journal Entry         | Journal Voucher               | Journal              |
| Bank Transaction      | Journal Voucher               | Journal              |

---

## Installation

### Prerequisites
- ERPNext v16 (Frappe v16)
- Python 3.11+
- Tally Prime 4.x running on same LAN/machine
- Tally XML Server enabled (see below)

### Step 1 — Get the app

```bash
cd /home/frappe/frappe-bench
bench get-app tally_bridge /path/to/tally_bridge
# OR if you publish it to GitHub:
# bench get-app https://github.com/yourorg/tally_bridge
```

### Step 2 — Install on your site

```bash
bench --site your-site.localhost install-app tally_bridge
bench --site your-site.localhost migrate
bench restart
```

### Step 3 — Enable Tally XML Server

In **Tally Prime**:
1. Open your company data
2. Go to **Gateway of Tally → F12: Configure**
3. Click **Advanced Configuration**
4. Set **Enable XML Server** → Yes
5. Set **Port** → 9000 (default)
6. Accept and restart Tally if prompted

### Step 4 — Configure Tally Bridge

Go to **Tally Settings** in ERPNext:

```
ERPNext Menu → Tally Bridge → Tally Settings
```

Fill in:
- **Tally Host**: `localhost` (or the IP if Tally is on another machine)
- **Tally Port**: `9000`
- **Company Name in Tally**: Must exactly match the company name in Tally
- **Sundry Debtors / Creditors Ledger**: Must match your Tally group names

### Step 5 — Test the connection

1. Go to **Tally Bridge Dashboard** (`/tally_bridge` in your browser)
2. Click **Test Connection**
3. You should see a success response with company list from Tally

---

## Usage

### From the Dashboard (bulk export)

Navigate to `/tally_bridge` or **Tally Bridge → Export Dashboard**:

1. Set your date range
2. Choose format: **Tally XML** (recommended), Excel, or JSON
3. Enable **Push directly to Tally** for automatic import
4. Click the export card you need (or **Export All**)

### From individual documents

Open any submitted Sales Invoice / Purchase Invoice / Payment Entry / Journal Entry:
- **Tally → Export to Tally**: pushes directly to Tally Prime
- **Tally → Download XML**: downloads the XML file for manual import

### Manual XML import into Tally

If you prefer to import manually:
1. Download the XML file from the dashboard or document
2. In Tally Prime: **Gateway of Tally → Import → Data**
3. Select the XML file
4. Choose your company
5. Accept

### Auto Sync (scheduled)

1. Enable **Auto Sync** in Tally Settings
2. Choose interval: Hourly, Every 6 Hours, or Daily
3. The scheduler will automatically push today's data to Tally

---

## File structure

```
tally_bridge/
├── tally_bridge/
│   ├── api/
│   │   ├── export.py          # All whitelisted API methods
│   │   └── sync.py            # Scheduled sync jobs
│   ├── doctype/
│   │   ├── tally_settings/    # Single doctype for config
│   │   └── tally_export_log/  # Log of every export
│   ├── templates/
│   │   └── pages/
│   │       └── tally_bridge.html  # Export dashboard UI
│   ├── public/js/
│   │   ├── sales_invoice_tally.js
│   │   └── purchase_invoice_tally.js  # Also includes PE & JE
│   └── utils/
│       ├── xml_generator.py   # Core Tally XML generation
│       ├── excel_exporter.py  # Excel export
│       └── tally_connector.py # HTTP connector to Tally
├── hooks.py
├── setup.py
└── requirements.txt
```

---

## XML format reference

All XML follows Tally Prime 4.x TALLYMESSAGE format:

```xml
<?xml version='1.0' encoding='UTF-8'?>
<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>Your Company Name</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <!-- LEDGER or VOUCHER elements go here -->
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>
```

---

## Tally account group mapping

ERPNext root type is mapped to Tally primary groups:

| ERPNext Root | Tally Group         |
|--------------|---------------------|
| Asset        | Current Assets      |
| Liability    | Current Liabilities |
| Income       | Sales Accounts      |
| Expense      | Indirect Expenses   |
| Equity       | Capital Account     |

You can customise this mapping in `utils/xml_generator.py` → `_ACCOUNT_GROUP_MAP`.

---

## Troubleshooting

### "Cannot connect to Tally"
- Make sure Tally Prime is **open** and a company is loaded
- Check that XML Server is enabled (F12 → Advanced Config in Tally)
- Check firewall — port 9000 must be accessible
- If Tally is on another machine, use its IP address, not `localhost`

### "Ledger not found in Tally"
- Run **Chart of Accounts** and **Customers & Suppliers** exports first before voucher exports
- Tally requires ledgers to exist before vouchers that reference them

### "Company name mismatch"
- The company name in **Tally Settings** must exactly match (case-sensitive) the company name as it appears in Tally Prime

### Partial imports
- Check the **Tally Export Log** → **Tally Response** field for LINEERROR messages
- Common causes: duplicate voucher numbers, ledgers not created, date outside financial year

---

## Permissions

| Role             | Dashboard | Settings | Export Log |
|------------------|-----------|----------|------------|
| System Manager   | ✅ Full    | ✅ Full   | ✅ Full     |
| Accounts Manager | ✅ Full    | ✅ Full   | ✅ Read/Write |
| Accounts User    | ✅ Export  | ❌        | ✅ Read     |

---

## License

MIT — free to use, modify, and distribute.
