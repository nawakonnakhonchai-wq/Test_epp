# Usage: python3 tools/build_from_geojson.py <EPP_export.geojson> index.html [--write] [--report out.csv]
# Without --write it only reports what would change.
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
import csv, json, re, sys
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
EMPTY = {'', '-', 'None', 'none', 'null'}


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

if WRITE:
    dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(',', ':'))
    out = html[:i0] + dump(EST) + html[i1:j0] + dump(LU) + html[j1:k0] + dump(IG) + html[k1:]
    open(html_path, 'w', encoding='utf-8').write(out)
    print('written', html_path)
