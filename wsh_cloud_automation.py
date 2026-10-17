import os
import time
import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================================
# 1. ADVANCED SENSIBULL DYNAMIC CHECKBOX & TICK-WISE OI EXTRACTOR
# ==========================================================
def extract_sensibull_oi_all_indices(driver):
    indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]
    combined_oi_payload = "\n\n==================================================\n"
    combined_oi_payload += "SENSIBULL DYNAMIC OPEN INTEREST & TICK-WISE CHANGE EXTRACTION (ALL 4 INDICES)\n"
    combined_oi_payload += "==================================================\n"
    
    for symbol in indices:
        url = f"https://web.sensibull.com/open-interest/oi-vs-strike?tradingsymbol={symbol}"
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Scraping dynamic OI & tick changes for: {symbol}")
        
        try:
            driver.get(url)
            time.sleep(5)
            
            combined_oi_payload += f"\n\n--- INDEX TARGET: {symbol} ---\n"
            
            checkboxes = driver.find_elements(By.XPATH, "//input[@type='checkbox']")
            labels = driver.find_elements(By.XPATH, "//input[@type='checkbox']/following-sibling::span | //input[@type='checkbox']/ancestor::label")
            
            if not checkboxes:
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                combined_oi_payload += soup.get_text(separator=' ')[:2000] + "\n"
                continue
                
            for cb in checkboxes:
                try:
                    if cb.is_selected():
                        driver.execute_script("arguments[0].click();", cb)
                        time.sleep(0.3)
                except Exception:
                    pass

            for idx in range(len(checkboxes)):
                try:
                    current_boxes = driver.find_elements(By.XPATH, "//input[@type='checkbox']")
                    current_labels = driver.find_elements(By.XPATH, "//input[@type='checkbox']/following-sibling::span | //input[@type='checkbox']/ancestor::label")
                    
                    expiry_name = current_labels[idx].text if idx < len(current_labels) else f"Expiry #{idx+1}"
                    
                    driver.execute_script("arguments[0].click();", current_boxes[idx])
                    time.sleep(2)
                    
                    soup = BeautifulSoup(driver.page_source, 'html.parser')
                    summary_text = ""
                    for element in soup.find_all(['div', 'span', 'p']):
                        txt = element.get_text(strip=True)
                        if any(k in txt for k in ["Total Call OI", "Total Put OI", "PCR", "Change", symbol]):
                            summary_text += txt + " | "
                    
                    combined_oi_payload += f"\n[EXPIRY CHECKBOX: {expiry_name}]\n"
                    combined_oi_payload += summary_text[:2000] + "\n"
                    
                    driver.execute_script("arguments[0].click();", current_boxes[idx])
                    time.sleep(0.3)
                    
                except Exception as e:
                    combined_oi_payload += f"\n[Error clicking checkbox #{idx+1} for {symbol}: {e}]\n"

        except Exception as e:
            combined_oi_payload += f"\n[Error loading OI page for {symbol}: {e}]\n"

    return combined_oi_payload

# ==========================================================
# 2. SENSIBULL FII/DII 4-TAB AUTOMATED EXTRACTOR
# ==========================================================
def extract_sensibull_fii_dii_tabs(driver):
    payload = f"\n\n==================================================\n"
    payload += f"SENSIBULL FII/DII DATA (ALL 4 TABS: Summary, F&O, Cash, History)\n"
    payload += f"==================================================\n"
    try:
        driver.get("https://web.sensibull.com/fii-dii-data")
        time.sleep(5)
        
        tabs = ["Summary", "Futures and Options", "Cash Market", "FII History"]
        
        for tab_name in tabs:
            payload += f"\n\n--- FII/DII TAB TARGET: {tab_name} ---\n"
            try:
                tab_element = driver.find_element(By.XPATH, f"//a[contains(text(), '{tab_name}')] | //div[contains(text(), '{tab_name}')] | //span[contains(text(), '{tab_name}')]")
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(3)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                tables = soup.find_all('table')
                if tables:
                    for idx, tbl in enumerate(tables):
                        payload += f"\n[Table #{idx + 1} for {tab_name}]\n"
                        for row in tbl.find_all('tr'):
                            cols = row.find_all(['td', 'th'])
                            r_data = [c.get_text(strip=True) for c in cols]
                            if r_data:
                                payload += " | ".join(r_data) + "\n"
                else:
                    payload += soup.get_text(separator=' | ')[:3000] + "\n"
                    
            except Exception as e:
                payload += f"\n[Could not click/extract FII/DII tab {tab_name}: {e}]\n"
                
    except Exception as e:
        payload += f"\n[Error loading FII/DII portal: {e}]\n"
        
    return payload

# ==========================================================
# 3. TRADINGVIEW DUAL EXTRACTOR (WATCHLIST + ON-CHART FUTURES OI TABLES)
# ==========================================================
def extract_tradingview_full_data(driver, url):
    payload = f"\n\n==================================================\n"
    payload += f"TRADINGVIEW FULL EXTRACTION (WATCHLIST + CHART TABLES): {url}\n"
    payload += f"==================================================\n"
    try:
        driver.get(url)
        time.sleep(7)
        
        watchlist_container = None
        selectors = [
            "div[data-name='watchlist-content']",
            "div.container-OWm9IZvM",
            "div.widgets-container",
            "div.right-toolbar",
            "div[class*='watchlist']"
        ]
        for sel in selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            if elements:
                watchlist_container = elements[0]
                break
                
        if watchlist_container:
            last_height = driver.execute_script("return arguments[0].scrollHeight", watchlist_container)
            for _ in range(12):
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", watchlist_container)
                time.sleep(0.8)
                new_height = driver.execute_script("return arguments[0].scrollHeight", watchlist_container)
                if new_height == last_height:
                    break
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        chart_tables = soup.find_all('table')
        if chart_tables:
            payload += "\n[EXTRACTED ON-CHART FUTURES OPEN INTEREST TABLES & TICK-WISE BUILDUP GRID]\n"
            for idx, tbl in enumerate(chart_tables):
                payload += f"\n--- Chart Table Overlay #{idx + 1} ---\n"
                rows = tbl.find_all('tr')
                for row in rows:
                    cols = row.find_all(['td', 'th'])
                    row_data = [col.get_text(strip=True) for col in cols]
                    if row_data:
                        payload += " | ".join(row_data) + "\n"
        
        rows = soup.find_all(['tr', 'div'], class_=lambda x: x and ('row' in x.lower() or 'item' in x.lower() or 'table-row' in x.lower()))
        extracted_rows = set()
        for row in rows:
            text = row.get_text(separator=' | ', strip=True)
            if text and len(text) > 3:
                extracted_rows.add(text)
                
        if extracted_rows:
            payload += "\n[Right-Panel Watchlist & Index Values List]\n"
            for item in sorted(extracted_rows):
                payload += item + "\n"
            
    except Exception as e:
        payload += f"\n[Error extracting TradingView chart and panel data: {e}]\n"
        
    return payload

# ==========================================================
# 4. CCIL MULTI-TAB EXTRACTOR (MONEY MARKET, GSEC, FOREX, DERIVATIVES)
# ==========================================================
def extract_ccil_multi_tabs(driver):
    payload = f"\n\n==================================================\n"
    payload += f"CCIL MULTI-MARKET EXTRACTION (MONEY MARKET, GSEC, FOREX, DERIVATIVES)\n"
    payload += f"==================================================\n"
    try:
        driver.get("https://www.ccilindia.com/")
        time.sleep(5)
        
        tabs = ["Money Market", "Gsec", "Forex", "Derivatives"]
        
        for tab_name in tabs:
            payload += f"\n\n--- CCIL SUB-MARKET TAB: {tab_name} ---\n"
            try:
                tab_element = driver.find_element(By.XPATH, f"//a[contains(text(), '{tab_name}')] | //button[contains(text(), '{tab_name}')]")
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(3)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                tables = soup.find_all('table')
                if tables:
                    for idx, tbl in enumerate(tables):
                        payload += f"\n[Table #{idx + 1} for {tab_name}]\n"
                        for row in tbl.find_all('tr'):
                            cols = row.find_all(['td', 'th'])
                            r_data = [c.get_text(strip=True) for c in cols]
                            if r_data:
                                payload += " | ".join(r_data) + "\n"
                else:
                    payload += soup.get_text(separator=' | ')[:3000] + "\n"
                    
            except Exception as e:
                payload += f"\n[Could not click/extract tab {tab_name}: {e}]\n"
                
    except Exception as e:
        payload += f"\n[Error loading CCIL portal: {e}]\n"
        
    return payload

# ==========================================================
# 5. TRADINGECONOMICS PAGE EXTRACTOR
# ==========================================================
def extract_tradingeconomics_page(driver, url):
    payload = f"\n\n==================================================\n"
    payload += f"TRADINGECONOMICS EXTRACTION: {url}\n"
    payload += f"==================================================\n"
    try:
        driver.get(url)
        time.sleep(5)
        
        target_periods = ["Previous Week", "Previous Month", "1W", "1M", "Weekly", "Monthly"]
        for period in target_periods:
            try:
                elements = driver.find_elements(By.XPATH, f"//*[contains(text(), '{period}')]")
                for el in elements:
                    if el.is_displayed() and el.tag_name in ['a', 'button', 'span', 'div', 'label', 'li', 'input']:
                        driver.execute_script("arguments[0].click();", el)
                        time.sleep(2)
            except Exception:
                pass
                
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        if "calendar" in url:
            today = datetime.date.today()
            month_str = today.strftime("%B")
            day_num = str(today.day)
            year_str = str(today.year)
            payload += f"\n[Targeting Present Day Calendar: {month_str} {day_num}, {year_str}]\n"
            
            found_today = False
            for idx, tbl in enumerate(soup.find_all('table')):
                if month_str in tbl.get_text() and day_num in tbl.get_text():
                    found_today = True
                    payload += f"\n--- Today's Calendar Table #{idx + 1} ---\n"
                    for row in tbl.find_all('tr'):
                        cols = row.find_all(['td', 'th'])
                        r_data = [c.get_text(strip=True) for c in cols]
                        if r_data:
                            payload += " | ".join(r_data) + "\n"
            if not found_today:
                payload += "\n[Present-Day Calendar Text Nodes]\n"
                for el in soup.find_all(['tr', 'div', 'h3']):
                    txt = el.get_text(strip=True)
                    if month_str in txt and day_num in txt:
                        payload += txt + "\n"
        else:
            tables = soup.find_all('table')
            if tables:
                for idx, tbl in enumerate(tables):
                    payload += f"\n[Table #{idx + 1}]\n"
                    for row in tbl.find_all('tr'):
                        cols = row.find_all(['td', 'th'])
                        r_data = [c.get_text(strip=True) for c in cols]
                        if r_data:
                            payload += " | ".join(r_data) + "\n"
            
            payload += f"\n[Page Content Nodes]\n"
            for el in soup.find_all(['div', 'span', 'p', 'td']):
                txt = el.get_text(strip=True)
                if len(txt) > 3:
                    payload += txt + " | "
                    
    except Exception as e:
        payload += f"\n[Error extracting TradingEconomics page: {e}]\n"
        
    return payload

# ==========================================================
# 6. GENERAL ULTRA-DEEP DATA SCRAPER
# ==========================================================
def extract_deep_raw_data(urls, job_type):
    timestamp_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    aggregated_payload = f"=== WSH INSTITUTIONAL ULTRA-RAW DATA PAYLOAD [{job_type}] - {timestamp_str} ===\n\n"
    
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
        else:
            try:
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Deep scraping URL: {url}")
                driver.get(url)
                time.sleep(4)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                for element in soup(["script", "style", "noscript", "svg"]):
                    element.extract()
                    
                aggregated_payload += f"\n\n==================================================\n"
                aggregated_payload += f"SOURCE URL TARGET: {url}\n"
                aggregated_payload += f"==================================================\n"
                
                tables = soup.find_all('table')
                if tables:
                    aggregated_payload += f"\n[EXTRACTED TABLES DATA FOR {url}]\n"
                    for idx, table in enumerate(tables):
                        aggregated_payload += f"\n--- Table Structure #{idx + 1} ---\n"
                        rows = table.find_all('tr')
                        for row in rows:
                            cols = row.find_all(['td', 'th'])
                            row_data = [col.get_text(strip=True) for col in cols]
                            if row_data:
                                aggregated_payload += " | ".join(row_data) + "\n"
                
                aggregated_payload += f"\n[FULL COMPREHENSIVE TEXT & METRIC NODES]\n"
                all_text_elements = soup.find_all(['div', 'span', 'p', 'li', 'td', 'b', 'strong'])
                seen_texts = set()
                
                for el in all_text_elements:
                    txt = el.get_text(strip=True)
                    if txt and len(txt) > 1 and txt not in seen_texts:
                        seen_texts.add(txt)
                        aggregated_payload += txt + " | "
                
                aggregated_payload += "\n\n"
                
            except Exception as e:
                aggregated_payload += f"\n\n--- SOURCE URL: {url} [CRITICAL EXTRACTION ERROR: {e}] ---\n\n"

    driver.quit()
    return aggregated_payload

# ==========================================================
# 7. CORE JOB EXECUTION & CLOUD STORAGE
# ==========================================================
def execute_deep_wsh_job(job_name, source_urls):
    print(f"\n==================================================")
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] RUNNING CLOUD EXTRACTION: {job_name}")
    print(f"==================================================\n")
    
    raw_data_output = extract_deep_raw_data(source_urls, job_name)
    
    folder_path = "WSH_Automated_Reports"
    os.makedirs(folder_path, exist_ok=True)
    
    filename = f"{job_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    filepath = os.path.join(folder_path, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(raw_data_output)
        
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] COMPLETE! Saved to Cloud Repo: {filepath}")

# ==========================================================
# 8. MASTER TARGET URL LISTS
# ==========================================================
WEBSITES_PRIE_INTRADAY = [
    "https://www.nseindia.com/market-data/index-performances",
    "https://www.nseindia.com/market-data/unchanged",
    "https://www.nseindia.com/market-data/top-gainers-losers",
    "https://www.research360.in/future-and-options/heatmap",
    "https://www.research360.in/future-and-options/sector-analysis",
    "https://www.niftytrader.in/nse-option-chain/nifty",
    "https://www.niftytrader.in/nse-option-chain/banknifty",
    "https://www.niftytrader.in/nse-option-chain/midcpnifty",
    "https://www.niftytrader.in/nse-option-chain/finnifty",
    "https://web.sensibull.com/open-interest/oi-vs-strike?tradingsymbol=NIFTY",
    "https://web.sensibull.com/open-interest/multistrike-oi?tradingsymbol=NIFTY",
    "https://web.sensibull.com/fii-dii-data",
    "https://in.tradingview.com/chart/xik75amh/?symbol=NSEIX%3ANIFTY1%21",
    "https://in.tradingview.com/chart/xik75amh/?symbol=NSE%3ANIFTY1%21",
    "https://in.tradingview.com/chart/xik75amh/?symbol=NSE%3ABANKNIFTY1%21"
]

WEBSITES_GSSE = [
    "https://www.ccilindia.com/",
    "https://fred.stlouisfed.org/series/BAMLH0A0HYM2",
    "https://fred.stlouisfed.org/series/CPFF",
    "https://fred.stlouisfed.org/series/VIXCLS",
    "https://www.newyorkfed.org/markets/reference-rates/sofr",
    "https://www.financialresearch.gov/financial-stress-index/",
    "https://tradingeconomics.com/commodity/baltic",
    "https://tradingeconomics.com/united-states/business-confidence",
    "https://www.tradingview.com/markets/currencies/cross-rates-overview-heat-map/",
    "https://tradingeconomics.com/bonds",
    "https://tradingeconomics.com/commodities/treemap?freq=daily",
    "https://tradingeconomics.com/commodities/treemap?freq=weekly",
    "https://tradingeconomics.com/commodities/treemap?freq=monthly",
    "https://tradingeconomics.com/commodities/treemap?freq=yearly",
    "https://in.tradingview.com/heatmap/stock/#%7B%22dataSource%22%3A%22AllEU%22%2C%22blockColor%22%3A%22change%22%2C%22blockSize%22%3A%22market_cap_basic%22%2C%22grouping%22%3A%22sector%22%7D",
    "https://www.tradingview.com/heatmap/stock/#%7B%22dataSource%22%3A%22SPX500%22%2C%22blockColor%22%3A%22change%22%2C%22blockSize%22%3A%22market_cap_basic%22%2C%22grouping%22%3A%22sector%22%7D",
    "https://in.tradingview.com/heatmap/crypto/#%7B%22dataSource%22%3A%22Crypto%22%2C%22blockColor%22%3A%2224h_close_change%7C5%22%2C%22blockSize%22%3A%22market_cap_calc%22%2C%22grouping%22%3A%22no_group%22%7D",
    "https://www.bloomberg.com/asia",
    "https://www.bloomberg.com/markets",
    "https://tradingeconomics.com/calendar"
]

# ==========================================================
# 9. DIRECT CLOUD EXECUTION TRIGGER
# ==========================================================
if __name__ == "__main__":
    print("==========================================================")
    print(" WSH Cloud Pipeline Initialized via GitHub Actions...")
    print("==========================================================")
    
    execute_deep_wsh_job("WSH_Intraday_UltraRaw", WEBSITES_PRIE_INTRADAY)
    execute_deep_wsh_job("WSH_GSSE_EOD_UltraRaw", WEBSITES_GSSE)
    
    print("==========================================================")
    print(" All Cloud Jobs Executed and Saved Successfully.")
    print("==========================================================")
