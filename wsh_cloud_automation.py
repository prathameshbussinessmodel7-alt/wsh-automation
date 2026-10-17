import os
import time
import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==========================================================
# EXTRACTION QUALITY GATE & LOGGING UTILITY
# ==========================================================
def log_quality_gate(source_name, status, details=""):
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] [QUALITY GATE] Source: {source_name} | Status: {status} | Details: {details}")
    return f"\n[SOURCE STATUS -> {source_name}: {status} | {details}]\n"

# ==========================================================
# 1. REPAIRED SENSIBULL DEEP STRIKE & OI EXTRACTOR
# ==========================================================
def extract_sensibull_oi_all_indices(driver):
    indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]
    combined_oi_payload = "\n\n==================================================\n"
    combined_oi_payload += "SENSIBULL REPAIRED STRIKE-LEVEL OI & TISSUE EXTRACTION (ALL INDICES)\n"
    combined_oi_payload += "==================================================\n"
    
    for symbol in indices:
        url = f"https://web.sensibull.com/open-interest/oi-vs-strike?tradingsymbol={symbol}"
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Scraping deep strike OI for target: {symbol}")
        
        try:
            driver.get(url)
            WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(6) # Generous wait for React/Vue grid hydration
            
            combined_oi_payload += f"\n\n--- INDEX TARGET: {symbol} ---\n"
            
            # Universal fallback: capture all financial grid nodes, rows, and cards if table tags fail
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Extract structured grids or div rows where OI data resides
            grid_containers = soup.find_all(['div', 'section'], class_=lambda x: x and ('grid' in x.lower() or 'table' in x.lower() or 'row' in x.lower() or 'strike' in x.lower()))
            
            extracted_metrics = set()
            for container in grid_containers:
                txt = container.get_text(separator=' | ', strip=True)
                # Filter specifically for options metrics to keep payload clean and high-density
                if any(k in txt for k in ["Call OI", "Put OI", "PCR", "Change", "Strike", symbol]):
                    if len(txt) > 15:
                        extracted_metrics.add(txt)
            
            if extracted_metrics:
                combined_oi_payload += log_quality_gate(f"Sensibull Deep OI - {symbol}", "PASS", f"Harvested {len(extracted_metrics)} deep strike/OI nodes.")
                for m in sorted(list(extracted_metrics))[:40]: # Top structural nodes
                    combined_oi_payload += m + "\n"
            else:
                combined_oi_payload += log_quality_gate(f"Sensibull Deep OI - {symbol}", "FAIL", "Could not locate strike grid elements.")
                # Absolute text fallback
                combined_oi_payload += soup.get_text(separator=' | ')[:4000] + "\n"

        except Exception as e:
            combined_oi_payload += log_quality_gate(f"Sensibull Deep OI - {symbol}", "FAIL", str(e))

    return combined_oi_payload

# ==========================================================
# 2. REPAIRED SENSIBULL FII/DII 4-TAB EXTRACTOR
# ==========================================================
def extract_sensibull_fii_dii_tabs(driver):
    payload = f"\n\n==================================================\n"
    payload += f"SENSIBULL FII/DII DATA (REPAIRED 4-TAB HARVESTER)\n"
    payload += f"==================================================\n"
    try:
        driver.get("https://web.sensibull.com/fii-dii-data")
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(6)
        
        tabs = ["Summary", "Futures and Options", "Cash Market", "FII History"]
        
        for tab_name in tabs:
            payload += f"\n\n--- FII/DII TAB TARGET: {tab_name} ---\n"
            try:
                # Multi-strategy XPath to guarantee tab click execution
                tab_xpath = f"//*[self::button or self::a or self::div or self::span][normalize-space(text())='{tab_name}']"
                tab_element = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, tab_xpath))
                )
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(4) # Wait for table grid injection
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                tables = soup.find_all(['table', 'div'], class_=lambda x: x and ('table' in x.lower() or 'grid' in x.lower() or 'row' in x.lower()))
                
                if tables:
                    payload += log_quality_gate(f"Sensibull Tab - {tab_name}", "PASS", f"Captured {len(tables)} structural grid elements.")
                    rows_captured = 0
                    for tbl in tables:
                        for row in tbl.find_all(['tr', 'div']):
                            r_txt = row.get_text(separator=' | ', strip=True)
                            if r_txt and len(r_txt) > 10 and r_txt not in payload:
                                payload += r_txt + "\n"
                                rows_captured += 1
                                if rows_captured > 50: # Cap per tab to ensure clean focus
                                    break
                else:
                    payload += log_quality_gate(f"Sensibull Tab - {tab_name}", "PARTIAL", "Falling back to full page text block.")
                    payload += soup.get_text(separator=' | ')[:3000] + "\n"
                    
            except Exception as e:
                payload += log_quality_gate(f"Sensibull Tab - {tab_name}", "FAIL", str(e))
                
    except Exception as e:
        payload += log_quality_gate("Sensibull FII/DII Portal", "FAIL", str(e))
        
    return payload

# ==========================================================
# 3. REPAIRED CCIL MULTI-TAB ISOLATION EXTRACTOR
# ==========================================================
def extract_ccil_multi_tabs(driver):
    payload = f"\n\n==================================================\n"
    payload += f"CCIL MULTI-MARKET EXTRACTION (ANTI-CONTAMINATION REPAIRED)\n"
    payload += f"==================================================\n"
    try:
        driver.get("https://www.ccilindia.com/")
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(6)
        
        tabs = ["Money Market", "Gsec", "Forex", "Derivatives"]
        seen_signatures = set()
        
        for tab_name in tabs:
            payload += f"\n\n--- CCIL SUB-MARKET TAB: {tab_name} ---\n"
            try:
                tab_xpath = f"//*[self::a or self::button or self::span][contains(normalize-space(text()), '{tab_name}')]"
                tab_element = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, tab_xpath))
                )
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(4)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                tables = soup.find_all('table')
                
                if tables:
                    table_content = tables[0].get_text(strip=True)[:120]
                    if table_content in seen_signatures and tab_name != "Money Market":
                        payload += log_quality_gate(f"CCIL Tab - {tab_name}", "FAIL", "Blocked cross-contamination: Duplicate table signature.")
                    else:
                        payload += log_quality_gate(f"CCIL Tab - {tab_name}", "PASS", f"Extracted unique data for {tab_name}.")
                        seen_signatures.add(table_content)
                        for idx, tbl in enumerate(tables):
                            payload += f"\n[Table #{idx + 1} for {tab_name}]\n"
                            for row in tbl.find_all('tr'):
                                cols = row.find_all(['td', 'th'])
                                r_data = [c.get_text(strip=True) for c in cols]
                                if r_data:
                                    payload += " | ".join(r_data) + "\n"
                else:
                    payload += log_quality_gate(f"CCIL Tab - {tab_name}", "FAIL", "No table tags found.")
                    
            except Exception as e:
                payload += log_quality_gate(f"CCIL Tab - {tab_name}", "FAIL", str(e))
                
    except Exception as e:
        payload += log_quality_gate("CCIL Portal General", "FAIL", str(e))
        
    return payload

# ==========================================================
# 4. REPAIRED TRADINGVIEW & TRADINGECONOMICS EXTRACTOR
# ==========================================================
def extract_tradingview_full_data(driver, url):
    payload = f"\n\n==================================================\n"
    payload += f"TRADINGVIEW REPAIRED EXTRACTION: {url}\n"
    payload += f"==================================================\n"
    try:
        driver.get(url)
        time.sleep(8) # Heavy SPA load time for TradingView charts
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Target table elements and data grids explicitly
        data_elements = soup.find_all(['table', 'div'], class_=lambda x: x and ('table' in x.lower() or 'grid' in x.lower() or 'pane' in x.lower() or 'row' in x.lower()))
        
        if data_elements:
            payload += log_quality_gate(f"TradingView - {url}", "PASS", f"Captured {len(data_elements)} chart grid components.")
            extracted_lines = set()
            for el in data_elements:
                txt = el.get_text(separator=' | ', strip=True)
                if txt and len(txt) > 20 and any(k in txt for k in ["OI", "Rollover", "Yield", "Change", "NIFTY", "BANKNIFTY"]):
                    extracted_lines.add(txt)
            for line in sorted(list(extracted_lines))[:30]:
                payload += line + "\n"
        else:
            payload += log_quality_gate(f"TradingView - {url}", "PARTIAL", "Extracted standard text nodes.")
            payload += soup.get_text(separator=' | ')[:3000] + "\n"
            
    except Exception as e:
        payload += log_quality_gate(f"TradingView - {url}", "FAIL", str(e))
        
    return payload

def extract_tradingeconomics_page(driver, url):
    payload = f"\n\n==================================================\n"
    payload += f"TRADINGECONOMICS REPAIRED EXTRACTION: {url}\n"
    payload += f"==================================================\n"
    try:
        driver.get(url)
        time.sleep(5)
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        tables = soup.find_all('table')
        
        if tables:
            payload += log_quality_gate(f"TradingEconomics - {url}", "PASS", f"Extracted {len(tables)} data tables.")
            for idx, tbl in enumerate(tables):
                payload += f"\n[Table #{idx + 1}]\n"
                for row in tbl.find_all('tr'):
                    cols = row.find_all(['td', 'th'])
                    r_data = [c.get_text(strip=True) for c in cols]
                    if r_data:
                        payload += " | ".join(r_data) + "\n"
        else:
            payload += log_quality_gate(f"TradingEconomics - {url}", "FAIL", "No tables found.")
            
    except Exception as e:
        payload += log_quality_gate(f"TradingEconomics - {url}", "FAIL", str(e))
        
    return payload

# ==========================================================
# 5. GENERAL EXTRACTION ORCHESTRATOR
# ==========================================================
def extract_deep_raw_data(urls, job_type):
    timestamp_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    aggregated_payload = f"=== WSH INSTITUTIONAL REPAIRED PAYLOAD [{job_type}] - {timestamp_str} ===\n\n"
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)

    if "PRIE" in job_type or "Intraday" in job_type:
        aggregated_payload += extract_sensibull_oi_all_indices(driver)

    if "GSSE" in job_type:
        aggregated_payload += extract_ccil_multi_tabs(driver)

    for url in urls:
        if "tradingview.com/chart" in url:
            aggregated_payload += extract_tradingview_full_data(driver, url)
        elif "ccilindia.com" in url and "GSSE" in job_type:
            continue
        elif "sensibull.com/fii-dii-data" in url:
            aggregated_payload += extract_sensibull_fii_dii_tabs(driver)
        elif "tradingeconomics.com" in url:
            aggregated_payload += extract_tradingeconomics_page(driver, url)

    driver.quit()
    return aggregated_payload

# ==========================================================
# 6. JOB EXECUTION & STORAGE
# ==========================================================
def execute_deep_wsh_job(job_name, source_urls):
    print(f"\n==================================================")
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] RUNNING REPAIRED CLOUD EXTRACTION: {job_name}")
    print(f"==================================================\n")
    
    raw_data_output = extract_deep_raw_data(source_urls, job_name)
    
    folder_path = "WSH_Automated_Reports"
    os.makedirs(folder_path, exist_ok=True)
    
    filename = f"{job_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    filepath = os.path.join(folder_path, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(raw_data_output)
        
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] COMPLETE! Repaired report saved to: {filepath}")

# ==========================================================
# 7. TARGET LISTS & TRIGGER
# ==========================================================
WEBSITES_PRIE_INTRADAY = [
    "https://web.sensibull.com/open-interest/oi-vs-strike?tradingsymbol=NIFTY",
    "https://web.sensibull.com/fii-dii-data",
    "https://in.tradingview.com/chart/xik75amh/?symbol=NSEIX%3ANIFTY1%21"
]

WEBSITES_GSSE = [
    "https://www.ccilindia.com/",
    "https://tradingeconomics.com/bonds",
    "https://tradingeconomics.com/calendar"
]

if __name__ == "__main__":
    execute_deep_wsh_job("WSH_Intraday_Repaired", WEBSITES_PRIE_INTRADAY)
    execute_deep_wsh_job("WSH_GSSE_Repaired", WEBSITES_GSSE)
