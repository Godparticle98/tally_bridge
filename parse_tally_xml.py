from lxml import etree
import sys

def parse_xml(filename):
    with open(filename, 'rb') as f:
        content = f.read()
    
    parser = etree.XMLParser(recover=True)
    try:
        content_str = content.decode('utf-16le')
        content_str = content_str.replace('encoding="utf-16"', 'encoding="utf-8"')
        root = etree.fromstring(content_str.encode('utf-8'), parser=parser)
    except Exception as e:
        print("Failed parsing as utf-16:", e)
        root = etree.fromstring(content, parser=parser)
        
    vouchers = root.findall(".//VOUCHER")
    for v in vouchers:
        print("Voucher Type:", v.get("VCHTYPE"))
        print("Is Invoice:", v.find("ISINVOICE").text if v.find("ISINVOICE") is not None else None)
        for inv in v.findall(".//ALLINVENTORYENTRIES.LIST"):
            print("  Item:", inv.find("STOCKITEMNAME").text if inv.find("STOCKITEMNAME") is not None else None, 
                  "Amount:", inv.find("AMOUNT").text if inv.find("AMOUNT") is not None else None,
                  "IsDeemedPositive:", inv.find("ISDEEMEDPOSITIVE").text if inv.find("ISDEEMEDPOSITIVE") is not None else None)
            for acc in inv.findall(".//ACCOUNTINGALLOCATIONS.LIST"):
                print("    Acc:", acc.find("LEDGERNAME").text if acc.find("LEDGERNAME") is not None else None, 
                      "Amount:", acc.find("AMOUNT").text if acc.find("AMOUNT") is not None else None,
                      "IsDeemedPositive:", acc.find("ISDEEMEDPOSITIVE").text if acc.find("ISDEEMEDPOSITIVE") is not None else None)
        for led in v.findall(".//LEDGERENTRIES.LIST"):
            print("  Ledger:", led.find("LEDGERNAME").text if led.find("LEDGERNAME") is not None else None, 
                  "Amount:", led.find("AMOUNT").text if led.find("AMOUNT") is not None else None,
                  "IsDeemedPositive:", led.find("ISDEEMEDPOSITIVE").text if led.find("ISDEEMEDPOSITIVE") is not None else None)
        print("-" * 40)

parse_xml("e:/DTI_Projects/Projects/AIWORKS/ERPNEXT_Tally_Connector/custom_app/tally_bridge/test/Transactions.xml")
