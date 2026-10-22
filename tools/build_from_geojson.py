# Usage: python3 tools/build_from_geojson.py <EPP_export.geojson> index.html [--write] [--report out.csv]
# Without --write it only reports what would change. --write also writes data/factories/<estate index>.json
# (factory details shown under the map; the page loads one estate's file when a parcel there is clicked).
"""Rebuild the parcel data (var EST / LU / IG) in index.html from a GeoJSON export of the
EPP_2026_V1 feature layer (ArcGIS Pro: Features To JSON, Output to GeoJSON + Project to WGS_1984).

- Only features that have a geometry and Measter_Plan = ผัง / โรงงาน go on the map.
  Features without geometry are skipped (listed in --report) and stay untouched in ArcGIS.
  Rows with no Measter_Plan are the related table (kam_Null.csv) that ArcGIS Pro adds to the export;
  they are ignored.
- Estate and parcel order from the current index.html is kept, new parcels are appended,
  so overrides saved in viewers' browsers keep pointing at the same parcels.
- If the layer has no company name for a parcel but the current page does (e.g. filled
  earlier with fill_names_by_cid.py), the current name is kept.
Needs: pip install shapely pyproj
"""
import csv, hashlib, json, os, re, sys
import numpy as np
from collections import Counter, defaultdict
from pyproj import Transformer
from shapely import STRtree, transform, union_all
from shapely.geometry import shape

args = [a for a in sys.argv[1:] if not a.startswith('--')]
gj_path, html_path = args[0], args[1]
WRITE = '--write' in sys.argv
REPORT = sys.argv[sys.argv.index('--report') + 1] if '--report' in sys.argv else None

PLANS = {'ผัง': 0, 'โรงงาน': 1}
STATUS = {'เป็นโรงงานที่มีการแจ้งเริ่มกิจการแล้ว': 0, 'เป็นโรงงานยังไม่แจ้งเริ่ม': 1,
          'หยุดประกอบกิจการ': 2, 'เลิกกิจการ': 3}          # anything else -> 4 (ไม่ระบุสถานะ)
OVERLAP_MIN = 0.20                                         # share of the smaller parcel
EMPTY = {'', '-', 'None', 'none', 'null', 'n/a', 'N/A'}


def txt(v):
    v = re.sub(r'\s+', ' ', '' if v is None else str(v)).strip()
    return '' if v in EMPTY else v


def cid(v):
    v = txt(v)
    return v if re.fullmatch(r'f\d{16,17}', v) else ''


def seg(s, name):
    i = s.index('var ' + name + '=') + len(name) + 5 if name == 'EST' else s.index(',' + name + '=') + len(name) + 2
    val, n = json.JSONDecoder().raw_decode(s[i:])
    return i, i + n, val


html = open(html_path, encoding='utf-8').read()
i0, i1, OLD = seg(html, 'EST')
j0, j1, LU = seg(html, 'LU')
k0, k1, IG = seg(html, 'IG')
LU, IG = list(LU), list(IG)
old_by_oid = {d[14]: (E['n'], d) for E in OLD for d in E['p']}
old_order = {E['n']: [d[14] for d in E['p']] for E in OLD}
old_origin = {E['n']: (E['lo'], E['la']) for E in OLD}

feats = json.load(open(gj_path, encoding='utf-8'))['features']
skipped, table_rows, bad_cid = [], 0, []
by_est = defaultdict(dict)
for f in feats:
    p = f['properties']
    plan = txt(p.get('Measter_Plan'))
    if plan not in PLANS:
        table_rows += 1
        continue
    if not f.get('geometry'):
        skipped.append(p)
        continue
    est = txt(p.get('IEAT')) or '(ไม่ระบุนิคม)'
    by_est[est][p['OBJECTID']] = f

to_utm = Transformer.from_crs(4326, 32647, always_xy=True)


def utm(g):
    return transform(g, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))


def idx(lst, v):
    if not v:
        return -1
    if v not in lst:
        lst.append(v)
    return lst.index(v)


est_names = [E['n'] for E in OLD if E['n'] in by_est] + sorted(n for n in by_est if n not in old_order)
# ---- dashboard stats per factory parcel: d[16] = [nat, boi, eia, land, risk, [hr x6] or 0]
FX = {'nat': [], 'boi': [], 'eia': [], 'land': ['ซื้อ / รับโอนกรรมสิทธิ์', 'เช่า / รับโอนสิทธิการเช่า',
                                               'ได้รับความยินยอมจากเจ้าของ', 'อื่น ๆ']}
HR = ['HrExecuiveTechnicianSum', 'HrMaleWorkersSkillSum', 'HrFemaleWorkersSkillSum',
      'HrMaleWorkersUnskillSum', 'HrFemaleWorkersUnskillSum', 'HrForeignTechnicianSum']


def land_group(v):
    if not v:
        return -1
    if 'ยินยอม' in v:
        return 2
    if 'เช่า' in v:
        return 1
    if 'ซื้อ' in v or 'กรรมสิทธิ์' in v:
        return 0
    return 3


def stat_row(p):
    nat = txt(p.get('NationalityFactory'))
    eia = txt(p.get('type_eia_ehia'))
    hr = [p.get(k) for k in HR]
    risk = p.get('risk_factory12')
    return [idx(FX['nat'], '' if nat == 'ไม่พบข้อมูล' else nat), idx(FX['boi'], txt(p.get('boi'))),
            idx(FX['eia'], eia), land_group(txt(p.get('LandAcquisition')) or txt(p.get('OwnerType'))),
            -1 if risk is None else int(risk),
            [int(v or 0) for v in hr] if any(v is not None for v in hr) else 0]


EST, stats = [], Counter()
for name in est_names:
    fs = by_est[name]
    order = [o for o in old_order.get(name, []) if o in fs] + sorted(o for o in fs if o not in old_order.get(name, []))
    geoms = [shape(fs[o]['geometry']) for o in order]
    if name in old_origin:      # keep the estate's grid origin so unchanged parcels encode identically
        lo, la = old_origin[name]
    else:
        lo = round(min(g.bounds[0] for g in geoms), 5)
        la = round(max(g.bounds[3] for g in geoms), 5)
    ug = [utm(g).buffer(0) for g in geoms]
    P = []
    for o, f, g in zip(order, (fs[o] for o in order), ug):
        p = f['properties']
        gm = f['geometry']
        polys = gm['coordinates'] if gm['type'] == 'MultiPolygon' else [gm['coordinates']]
        rings = []
        for poly in polys:
            for r in poly:
                flat, last = [], None
                for x, y in reversed(r):
                    q = (round((x - lo) * 1e5), round((la - y) * 1e5))
                    if q != last:
                        flat += q
                        last = q
                if len(flat) >= 8:          # at least 3 distinct points + closing point
                    rings.append(flat)
        plan = PLANS[txt(p['Measter_Plan'])]
        name_ = txt(p.get('mercant_name'))
        if not name_ and o in old_by_oid and old_by_oid[o][1][4]:
            name_ = old_by_oid[o][1][4]
            stats['name_kept_from_page'] += 1
        c = cid(p.get('CID'))
        if txt(p.get('CID')) and not c:
            bad_cid.append(p)
        P.append([round(g.area), rings, txt(p.get('Name_Parcel')), txt(p['Measter_Plan']), name_,
                  txt(p.get('factory_type_no')), idx(LU, txt(p.get('Discription'))), [],
                  STATUS.get(txt(p.get('cancel_production_status')), 4) if plan else -1, plan,
                  idx(IG, txt(p.get('industrial_group_desc'))), txt(p.get('factory_work_desc')),
                  txt(p.get('fid14n')), txt(p.get('mercant_id')), o, c])
        if plan:
            P[-1].append(stat_row(p))
        if o not in old_by_oid:
            stats['new_parcels'] += 1
    # overlaps (>= 20% of the smaller parcel), symmetric, largest share first
    tree = STRtree(ug)
    for a, b in zip(*tree.query(ug, predicate='intersects')):
        if a >= b:
            continue
        inter = ug[a].intersection(ug[b]).area
        small = min(ug[a].area, ug[b].area)
        pc = round(inter / small * 100) if small > 0 else 0
        if pc >= OVERLAP_MIN * 100:
            P[a][7] += [int(b), pc]
            P[b][7] += [int(a), pc]
    for d in P:
        prs = sorted(zip(d[7][::2], d[7][1::2]), key=lambda t: (-t[1], t[0]))
        d[7] = [v for t in prs for v in t]
    EST.append({'n': name, 'lo': lo, 'la': la, 'ua': round(union_all(ug).area), 'p': P})

removed = [o for o in old_by_oid if not any(o in by_est[n] for n in by_est)]
print(f"features in file={len(feats)} on map={sum(len(E['p']) for E in EST)} estates={len(EST)}")
print(f"skipped no geometry={len(skipped)} table rows ignored={table_rows}")
print(f"new parcels={stats['new_parcels']} removed parcels={len(removed)} "
      f"names kept from page={stats['name_kept_from_page']} bad CID values ignored={len(bad_cid)}")
for p in bad_cid:
    print('  BAD CID', p['OBJECTID'], p.get('IEAT'), repr(p.get('CID')))
if removed:
    print('  WARNING: parcels removed, browser overrides after them in the same estate shift:', removed[:20])

if REPORT:
    with open(REPORT, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['OBJECTID', 'IEAT', 'Measter_Plan', 'Name_Parcel', 'Name_Parcel_long', 'mercant_name', 'CID', 'สาเหตุ'])
        for p in skipped:
            w.writerow([p['OBJECTID'], txt(p.get('IEAT')), txt(p.get('Measter_Plan')), txt(p.get('Name_Parcel')),
                        txt(p.get('Name_Parcel_long')), txt(p.get('mercant_name')), txt(p.get('CID')), 'ไม่มีรูปแปลง'])
        for p in bad_cid:
            w.writerow([p['OBJECTID'], txt(p.get('IEAT')), txt(p.get('Measter_Plan')), txt(p.get('Name_Parcel')),
                        txt(p.get('Name_Parcel_long')), txt(p.get('mercant_name')), p.get('CID'), 'CID รูปแบบไม่ถูกต้อง'])
    print('report ->', REPORT)

# ---- factory details (data/factories/<e>.json): one row per factory parcel on the map, keyed by OBJECTID
FD_KEYS = ['mid', 'fac', 'typ', 'ftype', 'work', 'land', 'own', 'addr', 'farea', 'rai', 'ngan', 'wa',
           'stx', 'imp', 'act']


def num(v):
    if v is None or v == '':
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else round(f, 2)


def addr(p):
    parts = [txt(p.get('FactoryAddressGeneral'))]
    for lab, k in (('หมู่ ', 'FactoryAddressMoo'), ('ซ.', 'FactoryAddressSoi'), ('ถ.', 'FactoryAddressRoad'),
                   ('ต.', 'FactoryAddressTambonName'), ('อ.', 'FactoryAddressDistrictName'),
                   ('จ.', 'FactoryAddressProvinceName'), ('', 'FactoryAPostCode')):
        v = txt(p.get(k))
        if v:
            parts.append(v if v.startswith(lab.strip('. ')) and lab else lab + v)
    return ' '.join(x for x in parts if x)


def imp_date(v):
    from email.utils import parsedate_to_datetime
    v = txt(v)
    if not v:
        return None
    try:
        return parsedate_to_datetime(v).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return v[:10]


FD = {}
for e, name in enumerate(est_names):
    FD[e] = {}
    for o, f in by_est[name].items():
        p = f['properties']
        if txt(p['Measter_Plan']) != 'โรงงาน':
            continue
        row = [txt(p.get('mercant_id')), txt(p.get('FactoryID')), txt(p.get('factory_type_no')),
               txt(p.get('factory_work_desc')),
               txt(p.get('factory_work_desc_long')) or txt(p.get('factory_work_desc')),
               txt(p.get('LandAcquisition')), txt(p.get('OwnerType')), addr(p),
               num(p.get('factory_area_sqmeter')), num(p.get('RAI')), num(p.get('NGAN')), num(p.get('WA')),
               txt(p.get('cancel_production_status')), imp_date(p.get('import_date')),
               1 if txt(p.get('Active')).lower() == 'true' else 0]
        while row and row[-1] in ('', None):
            row.pop()
        FD[e][o] = [None if v == '' else v for v in row]
fd_files = {e: json.dumps({'k': FD_KEYS, 'r': r}, ensure_ascii=False, separators=(',', ':')) for e, r in FD.items()}
fd_hash = hashlib.md5(''.join(fd_files[e] for e in sorted(fd_files)).encode()).hexdigest()[:10]
print(f"factory details rows={sum(len(r) for r in FD.values())} files={len(fd_files)} "
      f"largest={max(len(v.encode()) for v in fd_files.values()) / 1e6:.2f} MB")

if WRITE:
    dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(',', ':'))
    out = html[:i0] + dump(EST) + html[i1:j0] + dump(LU) + html[j1:k0] + dump(IG) + html[k1:]
    QA = {'date': __import__('datetime').date.fromtimestamp(os.path.getmtime(gj_path)).isoformat(), 'nogeom': Counter(txt(p.get('IEAT')) or '(ไม่ระบุนิคม)' for p in skipped),
          'badcid': [[p['OBJECTID'], txt(p.get('IEAT')), txt(p.get('Name_Parcel')), re.sub(r'\s+', ' ', str(p.get('CID'))).strip()] for p in bad_cid]}
    out = re.sub(r'var QA=\{[^\n]*?\};', lambda m: 'var QA=' + dump(QA) + ';', out, count=1)
    out = re.sub(r'var FX=\{[^\n]*?\};', lambda m: 'var FX=' + dump(FX) + ';', out, count=1)
    fd_dir = os.path.join(os.path.dirname(os.path.abspath(html_path)), 'data', 'factories')
    os.makedirs(fd_dir, exist_ok=True)
    for fn in os.listdir(fd_dir):
        if fn.endswith('.json'):
            os.remove(os.path.join(fd_dir, fn))
    for e, js in fd_files.items():
        open(os.path.join(fd_dir, f'{e}.json'), 'w', encoding='utf-8').write(js)
    print('written', len(fd_files), 'files in', fd_dir)
    # cache-busting version the page appends when it fetches factory files
    out = re.sub(r'var FDV="[^"]*"', 'var FDV="' + fd_hash + '"', out)
    open(html_path, 'w', encoding='utf-8').write(out)
    print('written', html_path)
