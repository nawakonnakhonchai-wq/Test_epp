# Usage: python3 tools/fill_names_by_cid.py <file.csv> index.html [--write]
# Without --write it only reports. CSV columns: CID (no "f" prefix, 16-17 digits), ชื่อบริษัท(ภาษาไทย)
"""Fill company names into the e-PP page by CID (CSV column CID has no 'f' prefix).
Only fills parcels whose name is empty; reports conflicts instead of overwriting."""
import csv,json,re,sys
csv_path,html=sys.argv[1],sys.argv[2]; write='--write' in sys.argv
rows=list(csv.DictReader(open(csv_path,encoding='utf-8-sig')))
s=open(html,encoding='utf-8').read()
i=s.index('var EST=')+8; EST,end=json.JSONDecoder().raw_decode(s[i:])
by={}
for E in EST:
    for d in E['p']:
        if d[15]: by.setdefault(d[15],[]).append((E['n'],d))
filled=same=conflict=nomatch=bad=spaced=0
sq=lambda t: re.sub(r'\s+',' ',t).strip()
for r in rows:
    raw=r['CID'].strip(); name=sq(r['ชื่อบริษัท(ภาษาไทย)'])
    if not re.fullmatch(r'\d{16,17}',raw) or name in ('','-'): bad+=1; continue
    hits=by.get('f'+raw)
    if not hits: nomatch+=1; continue
    for est,d in hits:
        cur=(d[4] or '').strip()
        if not cur: d[4]=name; filled+=1; print('FILL',est,d[2],name)
        elif cur==name: same+=1
        elif sq(cur)==name: d[4]=name; spaced+=1; print('SPACES',est,d[2],repr(cur),'->',repr(name))
        else: conflict+=1; print('CONFLICT',est,d[2],repr(cur),'->',repr(name))
print(f'filled={filled} spaces_fixed={spaced} already_same={same} conflict={conflict} cid_not_found={nomatch} bad_rows={bad}')
if write and (filled or spaced):
    s=s[:i]+json.dumps(EST,ensure_ascii=False,separators=(',',':'))+s[i+end:]
    open(html,'w',encoding='utf-8').write(s); print('written')
