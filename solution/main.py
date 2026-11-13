from __future__ import annotations
import argparse, csv, hashlib, hmac, json, os, re, sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from collections import defaultdict

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / 'assignment-a-data' / 'schemas'
DB_PATH = Path(os.getenv('AU_DB_PATH', ROOT / 'au_ledger.sqlite3'))
def _key():
    value = os.getenv('AU_PII_KEY', '').encode()
    if not value:
        raise RuntimeError('AU_PII_KEY is required. Set a persistent secret before ingesting data.')
    return value

# Authenticated encryption is used when cryptography is installed. A stdlib fallback
# is deliberately not provided: storing PII without authenticated encryption is unsafe.
def _fernet():
    try:
        from cryptography.fernet import Fernet
        import base64
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_key()).digest()))
    except ImportError as exc:
        raise RuntimeError('Install cryptography: pip install cryptography') from exc

def encrypt(value):
    return _fernet().encrypt(str(value).encode()).decode() if value is not None else None

def decrypt(value):
    return _fernet().decrypt(value.encode()).decode() if value else None

def phone_digest(phone):
    return hmac.new(_key(), phone.encode(), hashlib.sha256).hexdigest()

def connect():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def init_db(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS leads (
      id INTEGER PRIMARY KEY, workflow_id TEXT NOT NULL, phone_hash TEXT NOT NULL,
      phone_cipher TEXT NOT NULL, phone_last4 TEXT NOT NULL, fields_json TEXT NOT NULL,
      batch_id TEXT NOT NULL, UNIQUE(workflow_id, phone_hash));
    CREATE TABLE IF NOT EXISTS events (
      event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, workflow_id TEXT NOT NULL,
      call_id TEXT, occurred_at TEXT, occurred_ist TEXT, phone_hash TEXT,
      payload_json TEXT NOT NULL, lead_id INTEGER REFERENCES leads(id),
      ignored INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS ix_events_call ON events(workflow_id, call_id);
    CREATE INDEX IF NOT EXISTS ix_events_lead ON events(lead_id);
    CREATE TABLE IF NOT EXISTS processed_drops(path TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS quality(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS rejections(workflow_id TEXT,batch_id TEXT,row_number INTEGER,reason TEXT,
      PRIMARY KEY(workflow_id,batch_id,row_number));
    ''')
    c.commit()

def schemas():
    out={}
    for p in sorted(SCHEMA_DIR.glob('*.json')):
        d=json.loads(p.read_text(encoding='utf-8')); out[d['workflow_id']]=d
    return out

def dispositions():
    d={}
    with (SCHEMA_DIR/'dispositions.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            d[(r['workflow_id'],r['code'].strip().lower())]={'terminal':r['is_terminal'].lower()=='true','success':r['is_success'].lower()=='true'}
    return d

def bump(c,key,n=1):
    c.execute('INSERT INTO quality(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value',(key,n))
def q(c,key):
    r=c.execute('SELECT value FROM quality WHERE key=?',(key,)).fetchone(); return r['value'] if r else 0

def norm_header(s): return re.sub(r'[_\s]+',' ',(s or '').strip().lower())
def norm_phone(v):
    digits=re.sub(r'\D','',str(v or ''))
    if len(digits)==12 and digits.startswith('91'): digits=digits[2:]
    elif len(digits)==11 and digits.startswith('0'): digits=digits[1:]
    if len(digits)!=10 or digits[0] not in '6789': raise ValueError('invalid_phone')
    return '+91'+digits

def parse_date(v):
    s=str(v).strip()
    for fmt in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%d-%b-%Y'):
        try: return datetime.strptime(s,fmt).date().isoformat()
        except ValueError: pass
    raise ValueError('invalid_date')
def parse_decimal(v):
    s=str(v).strip()
    s=re.sub(r'^(?:₹|rs\.?|inr)\s*','',s,flags=re.I).replace(',','')
    try: x=Decimal(s)
    except InvalidOperation: raise ValueError('invalid_decimal')
    if not x.is_finite() or x<0: raise ValueError('invalid_decimal')
    return str(x.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
def parse_field(v,f):
    s=(v or '').strip()
    if not s: return None
    t=f['type']
    if t=='string': return s
    if t=='phone': return norm_phone(s)
    if t=='date': return parse_date(s)
    if t=='decimal': return parse_decimal(s)
    if t=='enum':
        x=s.lower()
        if x not in [str(z).lower() for z in f.get('values',[])]: raise ValueError('invalid_enum')
        return x
    raise ValueError('unsupported_type:'+t)

def load_leads(c,drop,all_schemas):
    leadroot=drop/'leads'
    if not leadroot.exists(): return
    for wfdir in sorted(p for p in leadroot.iterdir() if p.is_dir()):
        wf=wfdir.name
        if wf not in all_schemas: continue
        spec=all_schemas[wf]; fields=spec['fields']
        aliases={norm_header(x):f['name'] for f in fields for x in [f['name'],*f.get('aliases',[])]}
        for file in sorted(wfdir.glob('*.csv')):
            batch=file.stem
            with file.open(encoding='utf-8-sig',newline='') as f:
                reader=csv.DictReader(f); mapped={h:aliases.get(norm_header(h)) for h in (reader.fieldnames or [])}
                rownum=0
                for raw in reader:
                    if not any((v or '').strip() for v in raw.values()): continue
                    rownum+=1; bump(c,'lead_rows_total')
                    vals={}; phone=None; reason=None; warnings=0
                    for field in fields:
                        name=field['name']; rawval=None
                        for h,target in mapped.items():
                            if target==name: rawval=raw.get(h); break
                        present=bool((rawval or '').strip())
                        try: val=parse_field(rawval,field)
                        except ValueError as e:
                            if name=='phone' and present: reason='invalid_phone'; break
                            val=None
                            if present: warnings+=1
                        if field.get('required') and val is None:
                            reason='missing_required:'+name; break
                        vals[name]=val
                        if name=='phone': phone=val
                    if reason:
                        bump(c,'lead_rows_rejected'); bump(c,'reject:'+reason)
                        c.execute('INSERT OR IGNORE INTO rejections VALUES(?,?,?,?)',(wf,batch,rownum,reason)); continue
                    bump(c,'field_warnings',warnings)
                    phash=phone_digest(phone)
                    old=c.execute('SELECT id,fields_json FROM leads WHERE workflow_id=? AND phone_hash=?',(wf,phash)).fetchone()
                    stored={}
                    for fld in fields:
                        n=fld['name']; value=vals.get(n)
                        if fld.get('pii') and value is not None: stored[n]={'_enc':encrypt(value)}
                        else: stored[n]=value
                    if old:
                        bump(c,'duplicate_lead_rows_merged')
                        prev=json.loads(old['fields_json'])
                        for fld in fields:
                            n=fld['name']; new=stored.get(n)
                            if new is not None: prev[n]=new
                        c.execute('UPDATE leads SET fields_json=? WHERE id=?',(json.dumps(prev),old['id']))
                    else:
                        c.execute('INSERT INTO leads(workflow_id,phone_hash,phone_cipher,phone_last4,fields_json,batch_id) VALUES(?,?,?,?,?,?)',
                          (wf,phash,encrypt(phone),phone[-4:],json.dumps(stored),batch))
    # Defaults only after all currently supplied batches have merged.
    for wf,spec in all_schemas.items():
        rows=c.execute('SELECT id,fields_json FROM leads WHERE workflow_id=?',(wf,)).fetchall()
        for r in rows:
            obj=json.loads(r['fields_json']); changed=False
            for fld in spec['fields']:
                if obj.get(fld['name']) is None and 'default' in fld:
                    obj[fld['name']]=fld['default']; changed=True
            if changed: c.execute('UPDATE leads SET fields_json=? WHERE id=?',(json.dumps(obj),r['id']))

def parse_ts(s):
    if not s: return None
    x=str(s).strip().replace('Z','+00:00')
    try: dt=datetime.fromisoformat(x)
    except ValueError: return None
    if dt.tzinfo is None: dt=dt.replace(tzinfo=IST)
    return dt.astimezone(IST)

def resolve_events(c):
    # Link phone-based events, then resolve phone-less overrides by call_id.
    rows=c.execute('SELECT event_id,workflow_id,call_id,phone_hash,event_type,payload_json FROM events WHERE lead_id IS NULL AND ignored=0').fetchall()
    for e in rows:
        lid=None
        if e['event_type']=='disposition.override' or not e['phone_hash']:
            x=c.execute('SELECT lead_id FROM events WHERE workflow_id=? AND call_id=? AND lead_id IS NOT NULL ORDER BY occurred_ist LIMIT 1',(e['workflow_id'],e['call_id'])).fetchone()
            if x: lid=x['lead_id']
        else:
            x=c.execute('SELECT id FROM leads WHERE workflow_id=? AND phone_hash=?',(e['workflow_id'],e['phone_hash'])).fetchone()
            if x: lid=x['id']
        if lid: c.execute('UPDATE events SET lead_id=? WHERE event_id=?',(lid,e['event_id']))

def load_events(c,drop,disp):
    eroot=drop/'events'
    if not eroot.exists(): return
    for file in sorted(eroot.glob('*.jsonl')):
        with file.open(encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                bump(c,'events_total')
                try: e=json.loads(line)
                except json.JSONDecodeError: bump(c,'malformed_events'); continue
                if not e.get('event_id'): bump(c,'malformed_events'); continue
                if c.execute('SELECT 1 FROM events WHERE event_id=?',(str(e['event_id']),)).fetchone(): bump(c,'duplicate_events_dropped'); continue
                wf=str(e.get('workflow_id') or ''); typ=str(e.get('event_type') or '')
                payload=e.get('payload') or {}; code=str(payload.get('disposition_code','')).lower()
                ignored=bool(typ.startswith('disposition.') and (wf,code) not in disp)
                if ignored: bump(c,'unknown_disposition_events')
                phone_hash=None
                if e.get('phone'):
                    try: phone_hash=phone_digest(norm_phone(e['phone']))
                    except ValueError: phone_hash='!invalid'
                dt=parse_ts(e.get('occurred_at'))
                c.execute('INSERT INTO events(event_id,event_type,workflow_id,call_id,occurred_at,occurred_ist,phone_hash,payload_json,ignored) VALUES(?,?,?,?,?,?,?,?,?)',
                  (str(e['event_id']),typ,wf,e.get('call_id'),e.get('occurred_at'),dt.isoformat() if dt else None,phone_hash,json.dumps(payload),int(ignored)))
    resolve_events(c)

def ingest(path):
    drop=Path(path).resolve(); c=connect(); init_db(c)
    key=str(drop)
    if c.execute('SELECT 1 FROM processed_drops WHERE path=?',(key,)).fetchone(): print(f'Skipped already ingested: {drop}'); c.close(); return
    ss=schemas(); ds=dispositions()
    try:
        load_leads(c,drop,ss); load_events(c,drop,ds); resolve_events(c)
        # Final orphan count is recomputed at report time; do not persist a cumulative count.
        c.execute('INSERT INTO processed_drops(path) VALUES(?)',(key,)); c.commit()
    except Exception: c.rollback(); raise
    finally: c.close()
    print(f'Ingested {drop}')

def reports(outdir):
    out=Path(outdir); out.mkdir(parents=True,exist_ok=True)
    c=connect(); init_db(c); ss=schemas(); ds=dispositions()
    leads=c.execute('SELECT * FROM leads ORDER BY workflow_id,batch_id').fetchall()
    evs=c.execute('SELECT * FROM events WHERE lead_id IS NOT NULL AND ignored=0 ORDER BY occurred_ist,event_id').fetchall()
    bylead=defaultdict(list); call_lead={}
    for e in evs:
        bylead[e['lead_id']].append(e)
        if e['call_id']: call_lead[(e['workflow_id'],e['call_id'])]=e['lead_id']
    # Effective disposition per call, overrides take precedence regardless of set timestamp.
    calls=defaultdict(list)
    for lid,items in bylead.items():
        grouped=defaultdict(list)
        for e in items:
            if e['call_id']: grouped[e['call_id']].append(e)
        for cid,es in grouped.items():
            initiated=[e for e in es if e['event_type']=='call.initiated']
            if not initiated: continue
            start=max(initiated,key=lambda x:x['occurred_ist'] or '')
            overrides=[e for e in es if e['event_type']=='disposition.override']
            sets=[e for e in es if e['event_type']=='disposition.set']
            chosen=max(overrides,key=lambda x:x['occurred_ist'] or '') if overrides else (max(sets,key=lambda x:x['occurred_ist'] or '') if sets else None)
            code=json.loads(chosen['payload_json']).get('disposition_code','').lower() if chosen else ''
            calls[lid].append({'cid':cid,'start':start['occurred_ist'],'connected':any(e['event_type']=='call.connected' for e in es),
              'telephony_failed':any(e['event_type']=='call.telephony_failed' for e in es),'disp':code,'workflow':start['workflow_id']})
    buckets={}; lead_info={}
    for l in leads:
        cs=calls.get(l['id'],[]); terminal=any(ds.get((l['workflow_id'],x['disp']),{}).get('terminal',False) for x in cs)
        if not cs: b='yet_to_reach_out'
        elif terminal: b='completed'
        elif all(x['telephony_failed'] for x in cs): b='in_progress_telephony_failed'
        else: b='in_progress_dialed'
        buckets[l['id']]=b
        effective_success=any(ds.get((l['workflow_id'],x['disp']),{}).get('success',False) for x in cs)
        sorted_calls=sorted(cs,key=lambda x:x['start'] or '')
        latest=next((x for x in reversed(sorted_calls) if x['disp']),None)
        fields=json.loads(l['fields_json']); ext=''
        for f in ss[l['workflow_id']]['fields']:
            if f.get('reference'):
                v=fields.get(f['name']); ext=decrypt(v['_enc']) if isinstance(v,dict) and '_enc' in v else (v or '')
        lead_info[l['id']]={'lead':l,'external':ext,'calls':cs,'bucket':b,'ever':effective_success,'latest':latest}
    # batch summary
    bh=defaultdict(lambda:defaultdict(int))
    for l in leads: bh[(l['workflow_id'],l['batch_id'])]['total_leads']+=1; bh[(l['workflow_id'],l['batch_id'])][buckets[l['id']]]+=1
    with (out/'batch_summary.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f,lineterminator='\n'); w.writerow(['workflow_id','batch_id','total_leads','yet_to_reach_out','in_progress_telephony_failed','in_progress_dialed','completed'])
        for (wf,batch),v in sorted(bh.items()): w.writerow([wf,batch,v['total_leads'],v['yet_to_reach_out'],v['in_progress_telephony_failed'],v['in_progress_dialed'],v['completed']])
    # daily overview
    daily=defaultdict(lambda:{'total':0,'connected':0,'success':set()})
    for lid,info in lead_info.items():
        for x in info['calls']:
            if not x['start']: continue
            day=x['start'][:10]; d=daily[(info['lead']['workflow_id'],day)]; d['total']+=1; d['connected']+=int(x['connected'])
            if ds.get((x['workflow'],x['disp']),{}).get('success'): d['success'].add(lid)
    with (out/'daily_overview.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f,lineterminator='\n'); w.writerow(['workflow_id','date','total_calls','connected_calls','connectivity_rate','success_leads'])
        for (wf,day),v in sorted(daily.items()): w.writerow([wf,day,v['total'],v['connected'],f"{v['connected']/v['total']:.4f}",len(v['success'])])
    # lead report
    with (out/'lead_report.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f,lineterminator='\n'); w.writerow(['workflow_id','external_ref','batch_id','phone_masked','bucket','total_calls','connected_calls','last_call_at','current_disposition','ever_success'])
        for lid,info in sorted(lead_info.items(),key=lambda z:(z[1]['lead']['workflow_id'],z[1]['external'])):
            l=info['lead']; cs=info['calls']; latest_start=max((x['start'] or '' for x in cs),default='')
            w.writerow([l['workflow_id'],info['external'],l['batch_id'],'+91******'+l['phone_last4'],info['bucket'],len(cs),sum(x['connected'] for x in cs),latest_start,info['latest']['disp'] if info['latest'] else '',str(info['ever']).lower()])
    with (out/'rejections.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f,lineterminator='\n'); w.writerow(['workflow_id','batch_id','row_number','reason'])
        for r in c.execute('SELECT * FROM rejections ORDER BY workflow_id,batch_id,row_number'): w.writerow([r['workflow_id'],r['batch_id'],r['row_number'],r['reason']])
    # Orphans are unique stored events not linkable after all currently ingested drops.
    orphan=c.execute('SELECT COUNT(*) n FROM events WHERE lead_id IS NULL AND ignored=0').fetchone()['n']
    quality={k:q(c,k) for k in ['lead_rows_total','lead_rows_rejected','duplicate_lead_rows_merged','field_warnings','events_total','malformed_events','duplicate_events_dropped','unknown_disposition_events']}
    quality['orphan_events']=orphan
    reasons={r['key'][7:]:r['value'] for r in c.execute("SELECT key,value FROM quality WHERE key LIKE 'reject:%'")}
    quality['rejections_by_reason']=dict(sorted(reasons.items()))
    (out/'data_quality.json').write_text(json.dumps(quality,indent=2)+'\n',encoding='utf-8')
    c.close(); print(f'Reports written to {out.resolve()}')

def main():
    p=argparse.ArgumentParser(description='Assignment A interaction ledger pipeline'); sub=p.add_subparsers(dest='cmd',required=True)
    i=sub.add_parser('ingest'); i.add_argument('drop')
    r=sub.add_parser('report'); r.add_argument('--out',required=True)
    a=p.parse_args()
    if a.cmd=='ingest': ingest(a.drop)
    else: reports(a.out)
if __name__=='__main__': main()
