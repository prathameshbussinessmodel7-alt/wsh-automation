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
# WSH QUALITY MANIFEST & DATA CONTRACT ENGINE
# ==========================================================
class QualityManifest:
    def __init__(self):
        self.manifest = {
            "execution_timestamp": datetime.datetime.now().isoformat(),
            "datasets": {}
        }

    def record_dataset(self, source, dataset_name, status, expected_fields, records_extracted, failure_reasons=None):
        self.manifest["datasets"][dataset_name] = {
            "source": source,
            "status": status, # PASS, PARTIAL, FAIL, SOURCE_UNAVAILABLE
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
        print(f"[MANIFEST SAVED] Quality audit written to {filepath}")

# ==========================================================
# 1. SENSIBULL OPTION CHAIN ADAPTER (PRIE)
# ==========================================================
def extract_sensibull_option_chains(driver, manifest_tracker):
    indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]
    payload = "\n\n==================================================\n"
    payload += "WSH PRIE: SENSIBULL OPTION-CHAIN INVENTORY (CONTRACT VALIDATED)\n"
    payload += "==================================================\n"
    
    for symbol in indices:
        url = f"https://web.sensibull.com/open-interest/oi-vs-strike?tradingsymbol={symbol}"
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] PRIE Adapter navigating to Sensibull OI for {symbol}")
        
        try:
            driver.get(url)
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(8) # Robust hydration wait for React SPA
            
            payload += f"\n\n--- PRIE INDEX TARGET: {symbol} ---\n"
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Semantic strike row extraction (Strict validation against UI controls)
            rows = soup.find_all(['tr', 'div'], class_=lambda x: x and ('row' in x.lower() or 'grid' in x.lower() or 'table' in x.lower()))
            
            valid_strike_rows = []
            for row in rows:
                txt = row.get_text(separator=' | ', strip=True)
                # Must contain numbers and options metrics, and explicitly exclude UI controls
                if ("|" in txt and any(char.isdigit() for char in txt)) and any(k in txt for k in ["Call OI", "Put OI", "PCR", "Change"]):
                    if not any(ui_clash in txt for ui_clash in ["Min", "Max", "Strike Count", "Expiry", "Refresh", "Login"]):
                        if len(txt) > 15:
                            valid_strike_rows.append(txt)
            
            # Quality Gate Decision per OpenAI contract specs
            if len(valid_strike_rows) >= 8:
                status = "PASS"
                reasons = []
                payload += f"\n[QUALITY GATE -> PASS | Index: {symbol} | Valid strike rows: {len(valid_strike_rows)}]\n"
                for r in valid_strike_rows[:40]:
                    payload += r + "\n"
            elif 0 < len(valid_strike_rows) < 8:
                status = "PARTIAL"
                reasons = ["Insufficient strike depth captured"]
                payload += f"\n[QUALITY GATE -> PARTIAL | Index: {symbol} | Insufficient rows: {len(valid_strike_rows)}]\n"
                for r in valid_strike_rows:
                    payload += r + "\n"
            else:
                status = "FAIL"
                reasons = ["Zero valid strike rows found; extracted UI controls or empty grid"]
                payload += f"\n[QUALITY GATE -> FAIL | Index: {symbol} | Zero valid strike rows detected]\n"
                payload += "[DEBUG FALLBACK TEXT]: " + soup.get_text(separator=' | ')[:1200] + "\n"
            
            manifest_tracker.record_dataset(
                source="Sensibull",
                dataset_name=f"option_chain_{symbol.lower()}",
                status=status,
                expected_fields=["strike", "call_oi", "put_oi", "call_change_oi", "put_change_oi"],
                records_extracted=len(valid_strike_rows),
                failure_reasons=reasons
            )

        except Exception as e:
            payload += f"\n[QUALITY GATE -> FAIL | Index: {symbol} | Exception: {str(e)}]\n"
            manifest_tracker.record_dataset("Sensibull", f"option_chain_{symbol.lower()}", "FAIL", ["strike", "call_oi", "put_oi"], 0, [str(e)])

    return payload

# ==========================================================
# 2. SENSIBULL FII/DII PARTICIPANT POSITIONING ADAPTER (PRIE)
# ==========================================================
def extract_sensibull_fii_dii_tabs(driver, manifest_tracker):
    payload = f"\n\n==================================================\n"
    payload += f"WSH PRIE: FII/DII PARTICIPANT POSITIONING (4-TAB CONTRACT)\n"
    payload += f"==================================================\n"
    
    tabs = ["Summary", "Futures and Options", "Cash Market", "FII History"]
    try:
        driver.get("https://web.sensibull.com/fii-dii-data")
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(7)
        
        for tab_name in tabs:
            payload += f"\n\n--- PRIE FII/DII TAB TARGET: {tab_name} ---\n"
            try:
                tab_xpath = f"//*[self::button or self::a or self::div or self::span][normalize-space(text())='{tab_name}']"
                tab_element = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, tab_xpath)))
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(4) # Panel render wait
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                tables = soup.find_all('table')
                
                rows_captured = 0
                if tables:
                    for idx, tbl in enumerate(tables):
                        tbl_txt = tbl.get_text(strip=True)
                        if len(tbl_txt) > 40:
                            payload += f"\n[Table #{idx + 1} for {tab_name}]\n"
                            for row in tbl.find_all('tr'):
                                cols = row.find_all(['td', 'th'])
                                r_data = [c.get_text(strip=True) for c in cols]
                                if r_data:
                                    payload += " | ".join(r_data) + "\n"
                                    rows_captured += 1
                
                if rows_captured > 0:
                    status = "PASS"
                    reasons = []
                    payload += f"\n[QUALITY GATE -> PASS | Tab: {tab_name} | Rows: {rows_captured}]\n"
                else:
                    status = "FAIL"
                    reasons = ["No unique table payload detected; rejecting generic text fallback"]
                    payload += f"\n[QUALITY GATE -> FAIL | Tab: {tab_name} | No valid rows extracted]\n"
                
                manifest_tracker.record_dataset("Sensibull", f"fii_dii_{tab_name.lower().replace(' ', '_')}", status, ["participant", "values", "date"], rows_captured, reasons)

            except Exception as e:
                payload += f"\n[QUALITY GATE -> FAIL | Tab: {tab_name} | Error: {str(e)}]\n"
                manifest_tracker.record_dataset("Sensibull", f"fii_dii_{tab_name.lower().replace(' ', '_')}", "FAIL", ["participant", "values"], 0, [str(e)])
                
    except Exception as e:
        payload += f"\n[QUALITY GATE -> FAIL | Portal Error: {str(e)}]\n"
        
    return payload

# ==========================================================
# 3. CCIL MULTI-MARKET ISOLATION ADAPTER (GSSE)
# ==========================================================
def extract_ccil_multi_tabs(driver, manifest_tracker):
    payload = f"\n\n==================================================\n"
    payload += f"WSH GSSE: CCIL MULTI-MARKET ISOLATION (ANTI-CONTAMINATION)\n"
    payload += f"==================================================\n"
    
    tabs = ["Money Market", "Gsec", "Forex", "Derivatives"]
    try:
        driver.get("https://www.ccilindia.com/")
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(7)
        
        for tab_name in tabs:
            payload += f"\n\n--- GSSE CCIL SUB-MARKET TAB: {tab_name} ---\n"
            try:
                tab_xpath = f"//*[self::a or self::button or self::span][contains(normalize-space(text()), '{tab_name}')]"
                tab_element = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, tab_xpath)))
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(4)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                tables = soup.find_all('table')
                
                if tables:
                    table_text = tables[0].get_text(strip=True)
                    
                    # Strict Contamination Check for Gsec
                    if tab_name == "Gsec" and any(k in table_text for k in ["CALL", "TREP", "SPECIAL REPO", "BASKET REPO"]):
                        status = "FAIL"
                        reasons = ["Contamination detected: Gsec table contains Money Market instruments"]
                        payload += f"\n[QUALITY GATE -> FAIL | CCIL {tab_name} | Cross-contamination detected]\n"
                    elif len(table_text) < 20:
                        status = "FAIL"
                        reasons = ["Extracted table is virtually empty"]
                        payload += f"\n[QUALITY GATE -> FAIL | CCIL {tab_name} | Empty table]\n"
                    else:
                        status = "PASS"
                        reasons = []
                        payload += f"\n[QUALITY GATE -> PASS | CCIL {tab_name} | Clean table extracted]\n"
                        for idx, tbl in enumerate(tables):
                            payload += f"\n[Table #{idx + 1} for {tab_name}]\n"
                            for row in tbl.find_all('tr'):
                                cols = row.find_all(['td', 'th'])
                                r_data = [c.get_text(strip=True) for c in cols]
                                if r_data:
                                    payload += " | ".join(r_data) + "\n"
                else:
                    status = "FAIL"
                    reasons = ["No table elements found"]
                    payload += f"\n[QUALITY GATE -> FAIL | CCIL {tab_name} | No tables rendered]\n"
                
                manifest_tracker.record_dataset("CCIL", f"ccil_{tab_name.lower().replace(' ', '_')}", status, ["instrument", "volume", "rate"], 1 if status=="PASS" else 0, reasons)

            except Exception as e:
                payload += f"\n[QUALITY GATE -> FAIL | CCIL {tab_name} | Error: {str(e)}]\n"
                manifest_tracker.record_dataset("CCIL", f"ccil_{tab_name.lower().replace(' ', '_')}", "FAIL", ["instrument", "rate"], 0, [str(e)])
                
    except Exception as e:
        payload += f"\n[QUALITY GATE -> FAIL | CCIL Portal General Error: {str(e)}]\n"
        
    return payload

# ==========================================================
# 4. TRADINGECONOMICS & TRADINGVIEW ADAPTERS (GSSE / PRIE)
# ==========================================================
def extract_tradingeconomics_page(driver, url, dataset_key, manifest_tracker):
    payload = f"\n\n==================================================\n"
    payload += f"WSH GSSE: TRADINGECONOMICS EXTRACTION: {url}\n"
    payload += f"==================================================\n"
    try:
        driver.get(url)
        time.sleep(6)
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        tables = soup.find_all('table')
        
        if tables:
            payload += f"\n[QUALITY GATE -> PASS | TradingEconomics {dataset_key} | Extracted {len(tables)} tables]\n"
            for idx, tbl in enumerate(tables):
                payload += f"\n[Table #{idx + 1}]\n"
                for row in tbl.find_all('tr'):
                    cols = row.find_all(['td', 'th'])
                    r_data = [c.get_text(strip=True) for c in cols]
                    if r_data:
                        payload += " | ".join(r_data) + "\n"
            manifest_tracker.record_dataset("TradingEconomics", dataset_key, "PASS", ["indicator", "actual", "previous"], len(tables))
        else:
            payload += f"\n[QUALITY GATE -> FAIL | TradingEconomics {dataset_key} | No tables found]\n"
            manifest_tracker.record_dataset("TradingEconomics", dataset_key, "FAIL", ["indicator", "actual"], 0, ["No tables found"])
            
    except Exception as e:
        payload += f"\n[QUALITY GATE -> FAIL | TradingEconomics {dataset_key} | Error: {str(e)}]\n"
        manifest_tracker.record_dataset("TradingEconomics", dataset_key, "FAIL", ["indicator"], 0, [str(e)])
        
    return payload

# ==========================================================
# 5. ORCHESTRATOR & PACKAGE WRITER
# ==========================================================
def execute_wsh_institutional_pipeline():
    timestamp_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    folder_path = "WSH_Automated_Reports"
    os.makedirs(folder_path, exist_ok=True)
    
    manifest = QualityManifest()
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)

    # Build PRIE Package
    prie_payload = f"=== WSH PRIE DATA PACKAGE [Indian Market Structure] - {timestamp_str} ===\n"
    prie_payload += extract_sensibull_option_chains(driver, manifest)
    prie_payload += extract_sensibull_fii_dii_tabs(driver, manifest)
    
    # Build GSSE Package
    gsse_payload = f"=== WSH GSSE DATA PACKAGE [Global Macro & Rates] - {timestamp_str} ===\n"
    gsse_payload += extract_ccil_multi_tabs(driver, manifest)
    gsse_payload += extract_tradingeconomics_page(driver, "https://tradingeconomics.com/bonds", "gsse_sovereign_bonds", manifest)
    gsse_payload += extract_tradingeconomics_page(driver, "https://tradingeconomics.com/calendar", "gsse_macro_calendar", manifest)

    driver.quit()

    # Save segregated institutional packages
    run_date_str = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    
    prie_path = os.path.join(folder_path, f"WSH_PRIE_Data_Package_{run_date_str}.txt")
    with open(prie_path, "w", encoding="utf-8") as f:
        f.write(prie_payload)
        
    gsse_path = os.path.join(folder_path, f"WSH_GSSE_Data_Package_{run_date_str}.txt")
    with open(gsse_path, "w", encoding="utf-8") as f:
        f.write(gsse_payload)
        
    manifest.save_manifest(folder_path)
    print(f"\n[PIPELINE COMPLETE] PRIE and GSSE packages successfully generated with immutable manifests.")

if __name__ == "__main__":
    execute_wsh_institutional_pipeline()
