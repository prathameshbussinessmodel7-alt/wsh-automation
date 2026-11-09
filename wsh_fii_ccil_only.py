"""WSH isolated FII/DII + CCIL collector."""
import os, re, json, time, hashlib, datetime, csv, io
from collections import Counter
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

PIPELINE_VERSION = "WSH_FII_CCIL_ISOLATED_R4_2026-11-09"
REPORT_DIR = "WSH_FII_CCIL_RND"
EVIDENCE_DIR = os.path.join(REPORT_DIR, "evidence")

class QualityManifest:
    def __init__(self):
        self.data = {"execution_timestamp": datetime.datetime.now().astimezone().isoformat(),
                     "pipeline_version": PIPELINE_VERSION,
                     "scope": ["NSE_FII_DII", "CCIL"], "datasets": {}}
    def record(self, source, key, publication, extraction, validation, fields, count, reasons=None, evidence=None):
        self.data["datasets"][key] = {
            "source": source, "publication_status": publication,
            "extraction_status": extraction, "validation_status": validation,
            "expected_fields": fields, "records_extracted": int(count),
            "failure_reasons": reasons or [], "evidence": evidence or {}
        }
    def save(self):
        os.makedirs(REPORT_DIR, exist_ok=True)
        path = os.path.join(REPORT_DIR, f"WSH_Quality_Manifest_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as f: json.dump(self.data, f, indent=2, ensure_ascii=False)
        return path

def norm(x): return re.sub(r"[^a-z0-9]", "", str(x or "").lower())
def sha(text): return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()
def save_evidence(key, raw, meta, ext):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", key)
    raw_path = os.path.join(EVIDENCE_DIR, f"{safe}_{stamp}.{ext}")
    meta_path = os.path.join(EVIDENCE_DIR, f"{safe}_{stamp}.json")
    mode = "wb" if isinstance(raw, bytes) else "w"
    with open(raw_path, mode) as f: f.write(raw if isinstance(raw, bytes) else (raw or ""))
    with open(meta_path, "w", encoding="utf-8") as f: json.dump(meta, f, indent=2, ensure_ascii=False, default=str)
    return {"raw": raw_path, "metadata": meta_path, "sha256": hashlib.sha256(raw if isinstance(raw, bytes) else (raw or "").encode()).hexdigest()}

def table_snapshot(html):
    soup = BeautifulSoup(html or "", "html.parser"); tables=[]; sigs=[]
    for i, table in enumerate(soup.find_all("table"), 1):
        rows=[]
        for tr in table.find_all("tr"):
            cells=[re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip() for c in tr.find_all(["th","td"])]
            if cells: rows.append(cells)
        if rows:
            fp=sha(json.dumps(rows, ensure_ascii=False, separators=(",",":")))
            tables.append({"index":i,"headers":rows[0],"rows":rows[1:],"row_count":len(rows)-1,"fingerprint":fp}); sigs.append(fp)
    return {"table_count":len(tables),"tables":tables,"fingerprint":sha("|".join(sorted(sigs)))}

def visible_controls(driver):
    out=[]
    for el in driver.find_elements(By.CSS_SELECTOR, "[role='tab'],button,a,[aria-controls],[role='button']"):
        try:
            if not el.is_displayed(): continue
            label=(el.get_attribute("aria-label") or el.get_attribute("title") or el.text or el.get_attribute("textContent") or "")
            label=re.sub(r"\s+"," ",label).strip()
            if label: out.append((label,el))
        except Exception: pass
    return out

def activate_tab(driver, label, timeout=10):
    before=table_snapshot(driver.page_source); controls=visible_controls(driver)
    matches=[el for text,el in controls if text.casefold()==label.casefold()]
    diag={"target":label,"exact_matches":len(matches),"clicked":False,"verified":False,
          "before_fingerprint":before["fingerprint"],"controls_seen":[t for t,_ in controls]}
    if len(matches)!=1:
        diag["reason"]="missing_or_ambiguous_exact_tab_control"; return diag
    el=matches[0]
    def active_state(x):
        selected=(x.get_attribute("aria-selected") or "").lower()
        if selected in ("true","false"): return selected=="true"
        cls=set((x.get_attribute("class") or "").lower().split())
        return bool(cls & {"active","selected","current","tab-active"})
    diag["before_active"]=active_state(el)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",el)
        diag["clicked"]=True
        def changed(_):
            snap=table_snapshot(driver.page_source); active=active_state(el)
            diag.update({"after_fingerprint":snap["fingerprint"],"after_active":active,
                         "table_set_changed":snap["fingerprint"]!=before["fingerprint"]})
            return snap["fingerprint"]!=before["fingerprint"] or (active and not diag["before_active"])
        WebDriverWait(driver,timeout).until(changed)
        diag["verified"]=True; diag["method"]="content_or_active_state_transition"
    except Exception as e:
        snap=table_snapshot(driver.page_source); active=active_state(el)
        diag.update({"after_fingerprint":snap["fingerprint"],"after_active":active,
                     "table_set_changed":snap["fingerprint"]!=before["fingerprint"],"wait_error":f"{type(e).__name__}: {e}"})
        if diag["table_set_changed"] or (active and not diag["before_active"]):
            diag["verified"]=True; diag["method"]="transition_observed_after_wait"
        else: diag["reason"]="no_observable_content_or_active_state_transition"
    return diag

# FII/DII: official NSE cash-market FII/FPI and DII activity endpoint.
# This is deliberately not the previous third-party tab model; unsupported datasets are not fabricated.
def extract_nse_fii_dii(driver, manifest):
    key="fii_dii_cash_activity"
    page="https://www.nseindia.com/reports/fii-dii"
    endpoint="https://www.nseindia.com/api/fiidiiTradeReact"
    out=["WSH FII/DII — OFFICIAL NSE CASH ACTIVITY",f"Source page: {page}",f"Data endpoint: {endpoint}"]
    try:
        driver.get("https://www.nseindia.com/")
        WebDriverWait(driver,15).until(EC.presence_of_element_located((By.TAG_NAME,"body")))
        driver.get(page); WebDriverWait(driver,15).until(EC.presence_of_element_located((By.TAG_NAME,"body")))
        result=driver.execute_async_script("""
          const url=arguments[0], done=arguments[arguments.length-1];
          fetch(url,{credentials:'include',headers:{'accept':'application/json,text/plain,*/*'}})
            .then(async r=>({http_status:r.status,content_type:r.headers.get('content-type'),text:await r.text()}))
            .then(done).catch(e=>done({error:String(e)}));
        """,endpoint)
        raw=result.get("text","")
        evidence={"page_url":driver.current_url,"endpoint":endpoint,"http_status":result.get("http_status"),
                  "content_type":result.get("content_type"),"fetch_error":result.get("error"),"raw_length":len(raw)}
        try: parsed=json.loads(raw) if raw else None
        except Exception: parsed=None
        records=[]
        if isinstance(parsed,list): records=parsed
        elif isinstance(parsed,dict):
            for v in parsed.values():
                if isinstance(v,list): records=v; break
        aliases={"participant":{"category","participant","investor","clienttype","name"},
                 "date":{"date","tradedate","asofdate"},
                 "buy":{"buyvalue","buy","purchasevalue"},
                 "sell":{"sellvalue","sell","salevalue"},
                 "net":{"netvalue","net","netbuyvalue"}}
        normalized=[]
        for r in records:
            if isinstance(r,dict):
                nr={norm(k):v for k,v in r.items()}; normalized.append(nr)
        field_hits={f:any(any(a in row for a in aa) for row in normalized) for f,aa in aliases.items()}
        usable=sum(1 for row in normalized if any(k in row for k in ["category","participant","clienttype","name"]))
        ev=save_evidence(key,raw,{**evidence,"record_count":len(normalized),"field_hits":field_hits,"sample":normalized[:5]},"json")
        out += ["",f"HTTP status: {result.get('http_status')}",f"Parsed records: {len(normalized)}",f"Field coverage: {json.dumps(field_hits)}",f"Evidence: {json.dumps(ev)}"]
        if result.get("http_status")==200 and normalized and usable and field_hits["participant"]:
            val="PARTIAL" if not all(field_hits.values()) else "PARTIAL"
            reasons=["Official NSE FII/DII activity response parsed. Retained as PARTIAL pending independent date, units and source reconciliation."]
            if not all(field_hits.values()): reasons.append("One or more buy/sell/net/date aliases were not found; raw response retained for schema mapping.")
            manifest.record("NSE",key,"PUBLISHED","SUCCESS",val,list(aliases),len(normalized),reasons,ev)
            out += ["STATUS: PUBLISHED / SUCCESS / PARTIAL",json.dumps(normalized[:20],ensure_ascii=False,default=str)]
        else:
            reasons=["NSE endpoint did not return a recognizable non-empty FII/DII record set.",f"HTTP={result.get('http_status')}",str(result.get('error') or "")]
            manifest.record("NSE",key,"UNKNOWN","FAILED","INVALID",list(aliases),0,reasons,ev)
            out += ["STATUS: UNKNOWN / FAILED / INVALID", "REASONS: "+"; ".join(reasons)]
    except Exception as e:
        reason=f"{type(e).__name__}: {e}"; manifest.record("NSE",key,"UNKNOWN","FAILED","INVALID",["participant","date","buy","sell","net"],0,[reason])
        out += ["STATUS: UNKNOWN / FAILED / INVALID",reason]
    # Explicitly declare unsupported historical and derivatives slices rather than pretending the cash API covers them.
    for k,desc in [("fii_dii_derivatives_participant","NSE participant-wise derivatives report mapping not yet configured"),
                   ("fii_dii_history","NSE historical archive mapping not yet configured")]:
        manifest.record("NSE",k,"UNKNOWN","NOT_ATTEMPTED","NOT_RUN",["source-specific fields","report date","units"],0,[desc])
        out += ["",f"{k}: NOT_ATTEMPTED — {desc}"]
    return "\n".join(out)+"\n"

# CCIL: retain each market as an independent page/tab contract and raw evidence stream.
def extract_ccil(driver, manifest):
    tabs=["Money Market","Gsec","Forex","Derivatives"]
    contracts={
      "Money Market":{"instrument":["instrument","product","segment","market","scheme","tenor"],"rate":["rate","weightedaveragerate","weighted average rate","interest rate","wavg rate"]},
      "Gsec":{"security":["security","securityname","isin","scrip","instrument","government security"],"yield_or_price":["yield","ytm","price","clean price","coupon"]},
      "Forex":{"pair_or_instrument":["currency pair","pair","currency","instrument","ccy"],"rate":["rate","exchange rate","forward rate","spot rate","buy rate","sell rate"]},
      "Derivatives":{"contract":["contract","instrument","underlying","product","contract name"],"expiry_or_value_date":["expiry","expiry date","value date","maturity","settlement date"]}}
    out=["WSH GSSE — CCIL FOUR-MARKET ISOLATION"]
    driver.get("https://www.ccilindia.com/"); WebDriverWait(driver,20).until(EC.presence_of_element_located((By.TAG_NAME,"body")))
    for tab in tabs:
        key="ccil_"+tab.lower().replace(" ","_"); out += ["",f"--- {tab} ---"]
        try:
            transition=activate_tab(driver,tab)
            html=driver.page_source; soup=BeautifulSoup(html,"html.parser"); snap=table_snapshot(html)
            matches=[]; diagnostics=[]; contamination=[]
            for table in soup.find_all("table"):
                rows=table.find_all("tr")
                if not rows: continue
                headers=[c.get_text(" ",strip=True) for c in rows[0].find_all(["th","td"])]
                if not headers: continue
                nh={norm(h) for h in headers}; coverage={f:[a for a in aliases if norm(a) in nh] for f,aliases in contracts[tab].items()}
                data=[[c.get_text(" ",strip=True) for c in r.find_all(["th","td"])] for r in rows[1:]]
                data=[r for r in data if any(x.strip() for x in r)]
                text=table.get_text(" ",strip=True).upper()
                markers=[m for m in ["TREPS","TREP","CALL MONEY","SPECIAL REPO","BASKET REPO","NOTICE MONEY","MONEY MARKET"] if m in text]
                if tab=="Gsec": contamination.extend(markers)
                diagnostics.append({"headers":headers,"rows":len(data),"coverage":coverage,"contamination":markers})
                if data and all(coverage.values()): matches.append({"headers":headers,"rows":data,"coverage":coverage})
            ev=save_evidence(key,html,{"url":driver.current_url,"transition":transition,"table_snapshot":snap,
                 "table_diagnostics":diagnostics,"gsec_contamination":sorted(set(contamination))},"html")
            n=sum(len(x["rows"]) for x in matches)
            if not transition["verified"]:
                ext,val="FAILED","INVALID"; reasons=["Exact target tab transition not verified; captured HTML is diagnostic only."]
            elif not snap["table_count"]:
                ext,val="FAILED","INVALID"; reasons=["No HTML tables present after tab selection."]
            elif tab=="Gsec" and contamination:
                ext,val="SUCCESS","QUARANTINED"; reasons=["GSEC table contains money-market contamination markers; rejected."]
            elif not matches:
                ext,val="SUCCESS","QUARANTINED"; reasons=["Tables rendered but no complete market-specific header contract matched; raw evidence retained."]
            else:
                ext,val="SUCCESS","PARTIAL"; reasons=["Market-specific header contract matched; units, observation date, instrument identity and independent reconciliation remain outstanding."]
            pub="PUBLISHED" if transition["verified"] and snap["table_count"] else "UNKNOWN"
            manifest.record("CCIL",key,pub,ext,val,sorted({a for g in contracts[tab].values() for a in g}),n,reasons,ev)
            out += [f"Transition: {json.dumps(transition,ensure_ascii=False,default=str)}",f"Tables: {snap['table_count']}; contract rows: {n}",f"Evidence: {json.dumps(ev)}",f"STATUS: {pub}/{ext}/{val}","REASONS: "+"; ".join(reasons)]
            for i,m in enumerate(matches,1):
                out += [f"TABLE {i}: "+" | ".join(m["headers"])]
                out += [" | ".join(r) for r in m["rows"]]
        except Exception as e:
            reason=f"{type(e).__name__}: {e}"; manifest.record("CCIL",key,"UNKNOWN","FAILED","INVALID",["market-specific schema"],0,[reason])
            out += [f"STATUS: UNKNOWN/FAILED/INVALID — {reason}"]
    return "\n".join(out)+"\n"

def main():
    os.makedirs(REPORT_DIR,exist_ok=True); manifest=QualityManifest()
    opts=Options(); opts.add_argument("--headless=new"); opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--disable-gpu"); opts.add_argument("--no-sandbox")
    opts.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36")
    driver=webdriver.Chrome(options=opts)
    try:
        fii=extract_nse_fii_dii(driver,manifest)
        ccil=extract_ccil(driver,manifest)
    finally: driver.quit()
    stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(REPORT_DIR,f"WSH_PRIE_FII_DII_{stamp}.txt"),"w",encoding="utf-8") as f: f.write(fii)
    with open(os.path.join(REPORT_DIR,f"WSH_GSSE_CCIL_{stamp}.txt"),"w",encoding="utf-8") as f: f.write(ccil)
    manifest_path=manifest.save()
    with open(os.path.join(REPORT_DIR,f"WSH_Source_Fingerprint_{stamp}.txt"),"w",encoding="utf-8") as f:
        f.write(f"pipeline_version={PIPELINE_VERSION}\nsource_file={os.path.basename(__file__)}\nsha256={hashlib.sha256(open(__file__,'rb').read()).hexdigest()}\n")
    counts=Counter(x["validation_status"] for x in manifest.data["datasets"].values())
    print(f"[FII-CCIL ONLY COMPLETE] datasets={len(manifest.data['datasets'])} validation={dict(counts)} manifest={manifest_path}")

if __name__=="__main__": main()
