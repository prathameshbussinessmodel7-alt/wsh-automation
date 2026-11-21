#!/usr/bin/env python3
"""WSH V3.11 bounded, checkpointed CDP WebSocket receipt capture.

Records browser-observed CDP payload representations and local receipt clocks;
not wire bytes, provider timestamps, or independent instrument identity.
"""
import argparse, hashlib, json, os, sys, time, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.driver_finder import DriverFinder
    from selenium.common.exceptions import TimeoutException, WebDriverException
except Exception as exc:
    raise SystemExit(f"Selenium import failed: {type(exc).__name__}: {exc}")

IST = timezone(timedelta(hours=5, minutes=30), name='IST')

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')

def atomic_json(path, obj):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='https://web.sensibull.com/option-chain')
    p.add_argument('--duration', type=int, default=180)
    p.add_argument('--session', required=True, choices=['A','B','C_RELOAD'])
    p.add_argument('--profile', default=str(Path.home()/'WSH_V3_11_Chrome_Profile'))
    p.add_argument('--out', default='WSH_V3_11_Captures')
    p.add_argument('--filter', default='sensibull', help='Socket URL substring; use ALL to disable filtering')
    p.add_argument('--expected-underlying', default='NIFTY')
    p.add_argument('--expected-expiry', required=True)
    p.add_argument('--start-at', help='Optional IST HH:MM schedule; omit to start immediately after confirmation')
    p.add_argument('--startup-timeout', type=int, default=35)
    p.add_argument('--page-timeout', type=int, default=35)
    args = p.parse_args()
    if not 30 <= args.duration <= 900: p.error('--duration must be 30..900 seconds')
    if not args.expected_expiry.strip(): p.error('--expected-expiry cannot be empty')
    if args.startup_timeout < 10 or args.startup_timeout > 120: p.error('--startup-timeout must be 10..120')
    if args.start_at:
        try: scheduled_clock = datetime.strptime(args.start_at, '%H:%M').time()
        except ValueError: p.error('--start-at must be HH:MM')
    else: scheduled_clock = None

    outdir = Path(args.out).resolve(); outdir.mkdir(parents=True, exist_ok=True)
    profile = Path(args.profile).resolve(); profile.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4()); stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    final_path = outdir / f'WSH_V3_11_Session_{args.session}_{stamp}.json'
    checkpoint_path = outdir / f'WSH_V3_11_Session_{args.session}_{stamp}.checkpoint.json'
    driver_log = outdir / f'chromedriver_{args.session}_{stamp}.log'
    frames, sockets, errors = [], {}, []
    state = {'schema':'WSH_V3_11_CAPTURE_1.1','run_id':run_id,'session':args.session,
      'status':'INITIALIZING','started_local_utc':utc_now(),'expected_underlying':args.expected_underlying.upper(),
      'expected_expiry':args.expected_expiry,'frames':frames,'socket_metadata':sockets,'errors':errors}
    atomic_json(checkpoint_path, state)
    driver = None
    service = Service(log_output=str(driver_log))
    options = Options()
    options.add_argument(f'--user-data-dir={profile}')
    options.add_argument('--no-first-run'); options.add_argument('--disable-notifications')
    options.add_argument('--disable-popup-blocking')
    options.page_load_strategy = 'eager'
    options.set_capability('goog:loggingPrefs', {'performance':'ALL','browser':'ALL'})
    os.environ.setdefault('SE_MANAGER_TIMEOUT', str(args.startup_timeout))
    print(f'[1/6] Starting ChromeDriver (bounded diagnostic log: {driver_log})', flush=True)
    try:
        # Resolve the ChromeDriver executable explicitly before starting Service.
        # This avoids 'Service path cannot be None' when automatic resolution does
        # not populate service.path in the installed Selenium/runtime combination.
        driver_path = DriverFinder(service, options).get_driver_path()
        if not driver_path or not Path(driver_path).is_file():
            raise RuntimeError(f'CHROMEDRIVER_RESOLUTION_FAILED: resolved path is not a file: {driver_path!r}')
        service.path = driver_path
        print(f'ChromeDriver executable resolved: {driver_path}', flush=True)
        driver = webdriver.Chrome(service=service, options=options)
        print('[2/6] ChromeDriver service and browser session started', flush=True)
        driver.set_page_load_timeout(args.page_timeout)
        print('[3/6] Browser session ready; opening Sensibull', flush=True)
        try:
            driver.get(args.url)
        except TimeoutException:
            errors.append('PAGE_LOAD_TIMEOUT: navigation exceeded configured timeout; browser stop-loading requested')
            try: driver.execute_script('window.stop();')
            except Exception as e: errors.append(f'STOP_LOADING_ERROR: {type(e).__name__}: {e}')
        print('[4/6] Page navigation attempted. Authenticate and select the requested chain.', flush=True)
        print(f"Required identity: {args.expected_underlying.upper()} | {args.expected_expiry}", flush=True)
        underlying = input('Type the displayed underlying exactly (NIFTY): ').strip().upper()
        expiry = input('Type the displayed expiry exactly (e.g. 23-Nov-2026): ').strip()
        if underlying != args.expected_underlying.upper() or expiry.casefold() != args.expected_expiry.casefold():
            raise RuntimeError('IDENTITY_MISMATCH: operator attestation did not match requested underlying/expiry')
        input('Confirm the correct option chain is visible and stable, then press ENTER: ')
        if scheduled_clock:
            scheduled = datetime.combine(datetime.now(IST).date(), scheduled_clock, tzinfo=IST)
            now = datetime.now(IST)
            if now > scheduled: raise RuntimeError(f'SCHEDULE_MISSED: armed {now.isoformat()} after {scheduled.isoformat()}')
            print(f'[5/6] ARMED for {scheduled.isoformat()}', flush=True)
            while datetime.now(IST) < scheduled: time.sleep(min(.25, max(.01,(scheduled-datetime.now(IST)).total_seconds())))
        actual_start = datetime.now(IST); start_utc = utc_now(); start_mono = time.monotonic_ns()
        deadline = time.monotonic() + args.duration
        state.update({'status':'CAPTURING','capture_start_local_utc':start_utc,
          'capture_start_ist':actual_start.isoformat(timespec='milliseconds'),
          'duration_requested_seconds':args.duration,'url_opened':args.url,'filter':args.filter})
        print(f'[6/6] CAPTURING {args.session} for {args.duration}s. Keep instrument/expiry unchanged.', flush=True)
        last_checkpoint = time.monotonic()
        while time.monotonic() < deadline:
            try: entries = driver.get_log('performance')
            except Exception as e:
                errors.append(f'PERFORMANCE_LOG_ERROR: {type(e).__name__}: {e}')
                raise
            for entry in entries:
                try: msg = json.loads(entry['message'])['message']
                except (KeyError, TypeError, ValueError): continue
                method, par = msg.get('method'), msg.get('params', {})
                rid = par.get('requestId')
                if not rid: continue
                if method == 'Network.webSocketCreated':
                    sockets.setdefault(rid, {}).update({'url':par.get('url',''),'created_local_utc':utc_now()})
                elif method == 'Network.webSocketWillSendHandshakeRequest':
                    sockets.setdefault(rid,{})['handshake_request_observed_utc'] = utc_now()
                elif method == 'Network.webSocketHandshakeResponseReceived':
                    r = par.get('response',{}); sockets.setdefault(rid,{})['handshake_response'] = {
                      'observed_local_utc':utc_now(),'status':r.get('status'),'protocol':r.get('protocol')}
                elif method == 'Network.webSocketFrameReceived':
                    resp = par.get('response',{}); sock = sockets.get(rid,{})
                    url = sock.get('url','')
                    if args.filter.upper() != 'ALL' and args.filter and args.filter.lower() not in url.lower(): continue
                    payload = resp.get('payloadData',''); payload_s = payload if isinstance(payload,str) else str(payload)
                    raw = payload_s.encode('utf-8',errors='replace')
                    frames.append({'session':args.session,'request_id':rid,'socket_url':url,
                      'opcode':resp.get('opcode'),'mask':resp.get('mask'),'local_receipt_utc':utc_now(),
                      'local_receipt_monotonic_ns':time.monotonic_ns(),
                      'payload_representation':'CDP_payloadData_text_representation',
                      'payload_length_chars':len(payload_s),'payload_sha256':hashlib.sha256(raw).hexdigest(),
                      'payload_data':payload_s})
            if time.monotonic() - last_checkpoint >= 5:
                state.update({'status':'CAPTURING','frame_count':len(frames),'socket_count_observed':len(sockets),
                  'checkpoint_local_utc':utc_now()})
                atomic_json(checkpoint_path,state); last_checkpoint=time.monotonic()
                print(f"Heartbeat: frames={len(frames)} sockets={len(sockets)}",flush=True)
            time.sleep(.20)
        end_utc=utc_now(); end_mono=time.monotonic_ns()
        result = {'schema':'WSH_V3_11_CAPTURE_1.1','run_id':run_id,'session':args.session,
          'identity_attestation':{'expected_underlying':args.expected_underlying.upper(),'expected_expiry':args.expected_expiry,
          'operator_entered_underlying':underlying,'operator_entered_expiry':expiry,
          'method':'manual_operator_attestation_not_independent_provider_verification'},
          'url_opened':args.url,'filter':args.filter,'duration_requested_seconds':args.duration,
          'capture_start_ist':actual_start.isoformat(timespec='milliseconds'),'capture_start_local_utc':start_utc,
          'capture_end_local_utc':end_utc,'capture_start_monotonic_ns':start_mono,'capture_end_monotonic_ns':end_mono,
          'elapsed_monotonic_seconds':round((end_mono-start_mono)/1e9,3),'frame_count':len(frames),
          'socket_count_observed':len(sockets),'socket_metadata':sockets,'frames':frames,'errors':errors,
          'driver_log':str(driver_log),'limitations':['CDP payloadData is a browser-exposed representation, not guaranteed wire bytes.',
          'Local UTC/monotonic clocks measure browser-side observation only.','No provider source timestamp or sequence semantics are inferred.',
          'Instrument/expiry identity is operator attestation, not independent provider verification.']}
        atomic_json(final_path,result)
        state.update({'status':'COMPLETE','final_file':str(final_path),'frame_count':len(frames),'socket_count_observed':len(sockets)})
        atomic_json(checkpoint_path,state)
        print(f'CAPTURE SAVED: {final_path}',flush=True)
        print(f"Frames={len(frames)} | sockets={len(sockets)} | driver log={driver_log}",flush=True)
        if not frames: print('WARNING: zero matching frames; capture is NOT accepted.',flush=True)
        return 0 if frames else 3
    except KeyboardInterrupt:
        errors.append('INTERRUPTED_BY_OPERATOR')
        state.update({'status':'INTERRUPTED','frame_count':len(frames),'socket_count_observed':len(sockets),'errors':errors})
        atomic_json(checkpoint_path,state); print('Interrupted; checkpoint retained.',flush=True); return 130
    except Exception as e:
        errors.append(f'{type(e).__name__}: {e}')
        state.update({'status':'FAILED','failed_local_utc':utc_now(),'frame_count':len(frames),
          'socket_count_observed':len(sockets),'errors':errors})
        atomic_json(checkpoint_path,state)
        print(f'CAPTURE FAILED: {type(e).__name__}: {e}',flush=True)
        print(f'Diagnostic checkpoint: {checkpoint_path}',flush=True)
        print(f'ChromeDriver log: {driver_log}',flush=True)
        return 2
    finally:
        if driver is not None:
            try: driver.quit()
            except Exception as e: print(f'WARNING driver.quit: {type(e).__name__}: {e}',flush=True)
        try: service.stop()
        except Exception: pass

if __name__ == '__main__':
    raise SystemExit(main())
