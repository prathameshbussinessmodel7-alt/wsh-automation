import os
import time
import datetime
import json
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==========================================================
# WSH MULTI-DIMENSIONAL QUALITY MANIFEST & CONTRACT ENGINE
# ==========================================================
class InstitutionalQualityManifest:
    def __init__(self):
        self.manifest = {
            "execution_timestamp": datetime.datetime.now().isoformat(),
            "datasets": {}
        }

    def record_dataset(self, source, dataset_name, pub_state, ext_state, val_state, expected_fields, records_extracted, failure_reasons=None):
        self.manifest["datasets"][dataset_name] = {
            "source": source,
            "publication_status": pub_state,  # PUBLISHED, MARKET_CLOSED, NOT_DUE
            "extraction_status": ext_state,    # SUCCESS, PARTIAL, FAILED, NOT_ATTEMPTED
            "validation_status": val_state,    # VALID, QUARANTINED, INVALID, NOT_RUN
            "expected_fields": expected_fields,
            "records_extracted": records_extracted,
            "failure_reasons": failure_reasons or []
        }

    def save_manifest(self, folder_path):
        os.makedirs(folder_path, exist_ok=True)
        filename = f"WSH_Quality_Manifest_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json"
        filepath = os.path.join(folder_path, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=4)
        print(f"[MANIFEST SAVED] Multi-dimensional quality audit written to {filepath}")

def is_market_trading_day():
    """Returns False if today is Saturday or Sunday, preventing false extraction failures."""
    current_day = datetime.datetime.now().weekday() # 5 = Saturday, 6 = Sunday
    return current_day < 5

# ==========================================================
# 1. SENSIBULL OPTION CHAIN ADAPTER (PRIE)
# ==========================================================
def extract_sensibull_option_chains(driver, manifest_tracker):
    indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]
    payload = "\n\n==================================================\n"
    payload += "WSH PRIE: SENSIBULL OPTION-CHAIN (MULTI-DIMENSIONAL AUDIT)\n"
    payload += "==================================================\n"
    
    is_trading = is_market_trading_day()
    pub_state = "PUBLISHED" if is_trading else "MARKET_CLOSED"

    for symbol in indices:
        url = f"https://web.sensibull.com/open-interest/oi-vs-strike?tradingsymbol={symbol}"
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] PRIE Adapter checking Sensibull OI for {symbol}")
        
        try:
            driver.get(url)
            WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(6)
            
            payload += f"\n\n--- PRIE INDEX TARGET: {symbol} ---\n"
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            rows = soup.find_all(['tr', 'div'], class_=lambda x: x and ('row' in x.lower() or 'grid' in x.lower() or 'table' in x.lower()))
            valid_strike_rows = []
            
            for row in rows:
                txt = row.get_text(separator=' | ', strip=True)
                if ("|" in txt and any(char.isdigit() for char in txt)) and any(k in txt for k in ["Call OI", "Put OI", "PCR", "Change"]):
                    if not any(ui_clash in txt for ui_clash in ["Min", "Max", "Strike Count", "Expiry", "Refresh", "Login"]):
                        if len(txt) > 15:
                            valid_strike_rows.append(txt)
            
            if len(valid_strike_rows) >= 8:
                ext_state, val_state, reasons = "SUCCESS", "VALID", []
                payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state} | Rows: {len(valid_strike_rows)}]\n"
                for r in valid_strike_rows[:35]:
                    payload += r + "\n"
            else:
                ext_state, val_state = ("SUCCESS" if is_trading else "NOT_ATTEMPTED"), "INVALID"
                reasons = ["Zero valid strike rows found; extracted UI controls or empty grid"]
                payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state} | Reason: {reasons[0]}]\n"
                payload += "[DEBUG FALLBACK TEXT]: " + soup.get_text(separator=' | ')[:1000] + "\n"
            
            manifest_tracker.record_dataset(
                source="Sensibull",
                dataset_name=f"option_chain_{symbol.lower()}",
                pub_state=pub_state,
                ext_state=ext_state,
                val_state=val_state,
                expected_fields=["strike", "call_oi", "put_oi", "call_change_oi", "put_change_oi"],
                records_extracted=len(valid_strike_rows),
                failure_reasons=reasons
            )

        except Exception as e:
            manifest_tracker.record_dataset("Sensibull", f"option_chain_{symbol.lower()}", pub_state, "FAILED", "INVALID", ["strike", "call_oi"], 0, [str(e)])

    return payload

# ==========================================================
# 2. SENSIBULL FII/DII PARTICIPANT POSITIONING ADAPTER (PRIE)
# ==========================================================
def extract_sensibull_fii_dii_tabs(driver, manifest_tracker):
    payload = f"\n\n==================================================\n"
    payload += f"WSH PRIE: FII/DII PARTICIPANT POSITIONING (TAB FINGERPRINTING)\n"
    payload += f"==================================================\n"
    
    tabs = ["Summary", "Futures and Options", "Cash Market", "FII History"]
    seen_content_signatures = set()
    pub_state = "PUBLISHED" if is_market_trading_day() else "MARKET_CLOSED"

    try:
        driver.get("https://web.sensibull.com/fii-dii-data")
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(6)
        
        for tab_name in tabs:
            payload += f"\n\n--- PRIE FII/DII TAB TARGET: {tab_name} ---\n"
            try:
                tab_xpath = f"//*[self::button or self::a or self::div or self::span][normalize-space(text())='{tab_name}']"
                tab_element = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, tab_xpath)))
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(4)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                tables = soup.find_all('table')
                
                rows_captured = 0
                table_signature = ""
                if tables:
                    table_signature = tables[0].get_text(strip=True)[:150]
                    
                    # Cross-Tab Fingerprinting Contamination Check
                    if table_signature in seen_content_signatures and tab_name != "Summary":
                        ext_state, val_state = "SUCCESS", "QUARANTINED"
                        reasons = ["Cross-tab fingerprint match: Identical table leaked across tabs."]
                        payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state} | Reason: {reasons[0]}]\n"
                    else:
                        seen_content_signatures.add(table_signature)
                        ext_state, val_state, reasons = "SUCCESS", "VALID", []
                        payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state}]\n"
                        for idx, tbl in enumerate(tables):
                            payload += f"\n[Table #{idx + 1} for {tab_name}]\n"
                            for row in tbl.find_all('tr'):
                                cols = row.find_all(['td', 'th'])
                                r_data = [c.get_text(strip=True) for c in cols]
                                if r_data:
                                    payload += " | ".join(r_data) + "\n"
                                    rows_captured += 1
                else:
                    ext_state, val_state = "FAILED", "INVALID"
                    reasons = ["No tables rendered."]
                    payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state}]\n"
                
                manifest_tracker.record_dataset("Sensibull", f"fii_dii_{tab_name.lower().replace(' ', '_')}", pub_state, ext_state, val_state, ["participant", "net_flow", "date"], rows_captured, reasons)

            except Exception as e:
                manifest_tracker.record_dataset("Sensibull", f"fii_dii_{tab_name.lower().replace(' ', '_')}", pub_state, "FAILED", "INVALID", ["participant"], 0, [str(e)])
                
    except Exception as e:
        payload += f"\n[Portal Error: {str(e)}]\n"
        
    return payload

# ==========================================================
# 3. CCIL MULTI-MARKET CALENDAR-AWARE ADAPTER (GSSE)
# ==========================================================
def extract_ccil_multi_tabs(driver, manifest_tracker):
    payload = f"\n\n==================================================\n"
    payload += f"WSH GSSE: CCIL MULTI-MARKET ISOLATION (CALENDAR-AWARE)\n"
    payload += f"==================================================\n"
    
    tabs = ["Money Market", "Gsec", "Forex", "Derivatives"]
    is_trading = is_market_trading_day()
    pub_state = "PUBLISHED" if is_trading else "MARKET_CLOSED"

    try:
        driver.get("https://www.ccilindia.com/")
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(6)
        
        for tab_name in tabs:
            payload += f"\n\n--- GSSE CCIL SUB-MARKET TAB: {tab_name} ---\n"
            
            # If weekend, handle as expected non-publication rather than script failure
            if not is_trading:
                payload += f"\n[STATUS -> PUB: MARKET_CLOSED | EXT: NOT_ATTEMPTED | VAL: NOT_RUN | Note: Weekend non-publication window]\n"
                manifest_tracker.record_dataset("CCIL", f"ccil_{tab_name.lower().replace(' ', '_')}", "MARKET_CLOSED", "NOT_ATTEMPTED", "NOT_RUN", ["instrument", "rate"], 0, ["Expected weekend non-publication"])
                continue

            try:
                tab_xpath = f"//*[self::a or self::button or self::span][contains(normalize-space(text()), '{tab_name}')]"
                tab_element = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, tab_xpath)))
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(4)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                tables = soup.find_all('table')
                
                if tables:
                    table_text = tables[0].get_text(strip=True)
                    if tab_name == "Gsec" and any(k in table_text for k in ["CALL", "TREP", "SPECIAL REPO", "BASKET REPO"]):
                        ext_state, val_state = "SUCCESS", "QUARANTINED"
                        reasons = ["Contamination detected: Gsec table contains Money Market instruments."]
                    elif len(table_text) < 20:
                        ext_state, val_state = "SUCCESS", "INVALID"
                        reasons = ["Extracted table is empty."]
                    else:
                        ext_state, val_state, reasons = "SUCCESS", "VALID", []
                        for idx, tbl in enumerate(tables):
                            payload += f"\n[Table #{idx + 1} for {tab_name}]\n"
                            for row in tbl.find_all('tr'):
                                cols = row.find_all(['td', 'th'])
                                r_data = [c.get_text(strip=True) for c in cols]
                                if r_data:
                                    payload += " | ".join(r_data) + "\n"
                    
                    payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state}]\n"
                else:
                    ext_state, val_state = "FAILED", "INVALID"
                    reasons = ["No table elements found."]
                    payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state}]\n"
                
                manifest_tracker.record_dataset("CCIL", f"ccil_{tab_name.lower().replace(' ', '_')}", pub_state, ext_state, val_state, ["instrument", "rate"], 1 if val_state=="VALID" else 0, reasons)

            except Exception as e:
                manifest_tracker.record_dataset("CCIL", f"ccil_{tab_name.lower().replace(' ', '_')}", pub_state, "FAILED", "INVALID", ["instrument", "rate"], 0, [str(e)])
                
    except Exception as e:
        payload += f"\n[CCIL Portal General Error: {str(e)}]\n"
        
    return payload

# ==========================================================
# 4. TRADINGECONOMICS ADAPTER (GSSE)
# ==========================================================
def extract_tradingeconomics_page(driver, url, dataset_key, manifest_tracker):
    payload = f"\n\n==================================================\n"
    payload += f"WSH GSSE: TRADINGECONOMICS EXTRACTION: {url}\n"
    payload += f"==================================================\n"
    pub_state = "PUBLISHED"
    try:
        driver.get(url)
        time.sleep(5)
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        tables = soup.find_all('table')
        
        if tables:
            payload += f"\n[STATUS -> PUB: {pub_state} | EXT: SUCCESS | VAL: VALID | Tables: {len(tables)}]\n"
            for idx, tbl in enumerate(tables):
                payload += f"\n[Table #{idx + 1}]\n"
                for row in tbl.find_all('tr'):
                    cols = row.find_all(['td', 'th'])
                    r_data = [c.get_text(strip=True) for c in cols]
                    if r_data:
                        payload += " | ".join(r_data) + "\n"
            manifest_tracker.record_dataset("TradingEconomics", dataset_key, pub_state, "SUCCESS", "VALID", ["indicator", "actual", "previous"], len(tables))
        else:
            manifest_tracker.record_dataset("TradingEconomics", dataset_key, pub_state, "FAILED", "INVALID", ["indicator"], 0, ["No tables found"])
            
    except Exception as e:
        manifest_tracker.record_dataset("TradingEconomics", dataset_key, pub_state, "FAILED", "INVALID", ["indicator"], 0, [str(e)])
        
    return payload

# ==========================================================
# 5. ORCHESTRATOR & PACKAGE WRITER
# ==========================================================
def execute_wsh_institutional_pipeline():
    timestamp_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    folder_path = "WSH_Automated_Reports"
    os.makedirs(folder_path, exist_ok=True)
    
    manifest = InstitutionalQualityManifest()
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)

    prie_payload = f"=== WSH PRIE DATA PACKAGE [Multi-Dimensional Audit] - {timestamp_str} ===\n"
    prie_payload += extract_sensibull_option_chains(driver, manifest)
    prie_payload += extract_sensibull_fii_dii_tabs(driver, manifest)
    
    gsse_payload = f"=== WSH GSSE DATA PACKAGE [Calendar-Aware Governance] - {timestamp_str} ===\n"
    gsse_payload += extract_ccil_multi_tabs(driver, manifest)
    gsse_payload += extract_tradingeconomics_page(driver, "https://tradingeconomics.com/bonds", "gsse_sovereign_bonds", manifest)
    gsse_payload += extract_tradingeconomics_page(driver, "https://tradingeconomics.com/calendar", "gsse_macro_calendar", manifest)

    driver.quit()

    run_date_str = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    
    with open(os.path.join(folder_path, f"WSH_PRIE_Data_Package_{run_date_str}.txt"), "w", encoding="utf-8") as f:
        f.write(prie_payload)
        
    with open(os.path.join(folder_path, f"WSH_GSSE_Data_Package_{run_date_str}.txt"), "w", encoding="utf-8") as f:
        f.write(gsse_payload)
        
    manifest.save_manifest(folder_path)
    print(f"\n[PIPELINE COMPLETE] Multi-dimensional institutional packages generated successfully.")

if __name__ == "__main__":
    execute_wsh_institutional_pipeline()
