"""2 km cells and reproducible point-reference comparison over the named graph."""
import importlib.util
import json
import math
import random
from pathlib import Path

from common import (WORK, OUT, SEED, GRID_METRES, LIMIT_SECONDS,
                    write_json, whole_minutes, nearest10, gzip_file)

# This upper bound is deliberately generous: top_speed=140 km/h means at most
# 210 km driving in 90 min, plus <=1 km snap at each endpoint. EPSG:5070's
# projected bounding margin is 250 km; candidates use geodesic <=212 km.
CANDIDATE_METRES = 212_000
PROJECTED_MARGIN = 250_000
AUTO = {'top_speed': 140, 'speed_types': ['freeflow']}


def polygon_from_poly(path):
    from shapely.geometry import Polygon, MultiPolygon
    rings = []
    current = []
    active = False
    hole = False
    for line in path.read_text().splitlines()[1:]:
        line = line.strip()
        if line == 'END':
            if active:
                rings.append((hole, current)); current = []; active = False
            else:
                break
        elif not active:
            hole = line.startswith('!'); active = True
        else:
            lon, lat = map(float, line.split()); current.append((lon, lat))
    shells = [Polygon(points) for is_hole, points in rings if not is_hole]
    for is_hole, points in rings:
        if is_hole:
            void = Polygon(points)
            shells = [p.difference(void) for p in shells]
    return MultiPolygon(shells)


def cell_key(x, y):
    return math.floor(x / GRID_METRES), math.floor(y / GRID_METRES)


def centre(key):
    return (key[0] + 0.5) * GRID_METRES, (key[1] + 0.5) * GRID_METRES


def location(point):
    return {'lat': float(point[0]), 'lon': float(point[1]),
            'search_cutoff': 1000, 'minimum_reachability': 0, 'radius': 0}


def request_base():
    return {'costing': 'auto', 'costing_options': {'auto': AUTO}, 'units': 'kilometers'}


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def snap(actor, point):
    """Only correlate within 1 km. No substituted coordinates or invented times."""
    from valhalla import ValhallaError
    try:
        result = decode(actor.locate({**request_base(), 'locations': [location(point)]}))
        item = result[0] if isinstance(result, list) else result['locations'][0]
        edges = item.get('edges', [])
        if not edges:
            return {'status': 'snap_failed'}
        return {'status': 'snapped'}
    except ValhallaError as error:
        return {'status': 'snap_failed', 'valhalla_code': error.code}


def matrix(actor, point, courses):
    """The cell centre is a SOURCE and courses are TARGETS on directed roads."""
    if not courses:
        return {}
    result = decode(actor.sources_to_targets({**request_base(),
        'sources': [location(point)],
        'targets': [location((c['destination']['latitude'], c['destination']['longitude'])) for c in courses]}))
    row = result['sources_to_targets'][0]
    if len(row) != len(courses):
        raise ValueError('Matrix target count mismatch')
    output = {}
    for c, pair in zip(courses, row):
        seconds = pair.get('time')
        if seconds is not None and (not math.isfinite(seconds) or seconds < 0):
            raise ValueError('Invalid matrix duration')
        output[c['course_id']] = seconds
    return output


def reference(actor, point, course):
    from valhalla import ValhallaError
    try:
        data = decode(actor.route({**request_base(), 'locations': [location(point),
            location((course['destination']['latitude'], course['destination']['longitude']))]}))
        seconds = data['trip']['summary']['time']
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Invalid reference duration')
        return {'status': 'routed', 'seconds': seconds}
    except ValhallaError as error:
        # Explicitly preserve all failures; do not silently call them unreachable.
        return {'status': 'route_error', 'valhalla_code': error.code}


def representative_points():
    # Public city centres, synthetic origins, never user locations.
    return [('chicago', 41.8818, -87.6232), ('rockford', 42.2711, -89.0940),
            ('quad-cities-border', 41.5067, -90.5151), ('quincy-border', 39.9356, -91.4099),
            ('east-st-louis-border', 38.6245, -90.1509), ('cairo-border', 37.0053, -89.1765),
            ('danville-border', 40.1245, -87.6300), ('springfield', 39.7817, -89.6501),
            ('champaign', 40.1164, -88.2434), ('peoria', 40.6936, -89.5890),
            ('effingham-rural-region', 39.1200, -88.5434), ('carbondale', 37.7273, -89.2168)]


def accuracy_summary(rows):
    eligible_pairs = [p for r in rows for p in r['pairs'] if p['reference_status'] == 'within_90']
    one = sum(p.get('absolute_error_minutes', math.inf) <= 1 for p in eligible_pairs)
    two = sum(p.get('absolute_error_minutes', math.inf) <= 2 for p in eligible_pairs)
    top_eligible = [r for r in rows if len(r['exact_nearest10']) == 10]
    ordered = sum(r['exact_nearest10'] == r['table_nearest10'] for r in top_eligible)
    return {'origins': len(rows), 'exact_within_90_pairs': len(eligible_pairs),
            'within_1_minute_count': one, 'within_2_minutes_count': two,
            'within_1_minute_share': one / len(eligible_pairs) if eligible_pairs else None,
            'within_2_minutes_share': two / len(eligible_pairs) if eligible_pairs else None,
            'nearest10_eligible_origins': len(top_eligible), 'nearest10_same_order_count': ordered,
            'nearest10_same_order_share': ordered / len(top_eligible) if top_eligible else None,
            'missing_table_for_exact_within90': sum(p.get('table_minutes') is None for p in eligible_pairs),
            'route_error_pairs': sum(p['reference_status'] == 'route_error' for r in rows for p in r['pairs']),
            'nearest10_definition': 'Exact point-to-course seconds then course_id vs encoded cell-centre minutes then course_id; top-10 ID sequences identical, exact >=10 within90 denominator; missing/short table fails',
            'error_definition': 'abs(encoded whole-minute centre time - exact point route seconds/60); exact <=5400s pairs denominator, missing centre entry fails both thresholds'}


def route_pilot():
    import valhalla
    from pyproj import Transformer, Geod
    from shapely.ops import transform
    from shapely.geometry import Point
    to_xy = Transformer.from_crs(4326, 5070, always_xy=True).transform
    to_ll = Transformer.from_crs(5070, 4326, always_xy=True).transform
    geod = Geod(ellps='WGS84')
    illinois = transform(to_xy, polygon_from_poly(WORK / 'illinois.poly'))
    actor = valhalla.Actor(str(WORK / 'valhalla.json'))
    courses = json.loads((OUT / 'courses.json').read_text())
    usable = []
    for c in courses:
        if c['destination']:
            c['snap'] = snap(actor, (c['destination']['latitude'], c['destination']['longitude']))
            if c['snap']['status'] == 'snapped':
                usable.append(c)
    write_json(OUT / 'courses.json', courses)
    if not usable:
        raise RuntimeError('No published Illinois destination can be snapped within 1 km')
    coords = [to_xy(c['destination']['longitude'], c['destination']['latitude']) for c in usable]
    # Every cell centre in this conservative rectangular envelope is considered;
    # only geodesically impossible origin/course pairs are skipped.
    ix0, iy0 = cell_key(min(x for x, y in coords) - PROJECTED_MARGIN,
                        min(y for x, y in coords) - PROJECTED_MARGIN)
    ix1, iy1 = cell_key(max(x for x, y in coords) + PROJECTED_MARGIN,
                        max(y for x, y in coords) + PROJECTED_MARGIN)
    table_dir = OUT / 'tables'; table_dir.mkdir()
    cache = {}
    counts = {'cells_considered': 0, 'cell_snap_failed': 0, 'matrix_null_pairs': 0,
              'pairs_within90': 0, 'grid_crs': 'EPSG:5070', 'cell_metres': GRID_METRES,
              'candidate_radius_metres': CANDIDATE_METRES, 'rounding': 'floor(seconds/60+0.5), raw <=5400 eligibility',
              'area_partition': '20x20 cells = 40 km squares; cell indices floor(EPSG:5070 coordinate/2000)',
              'scope': 'seven-state graph only; external detours and unretained roads are not proven covered',
              'snap_policy': 'Valhalla drivable edge correlation, search_cutoff=1000m, minimum_reachability=0; failure stored explicitly'}
    handles = {}
    def cell(point, key):
        candidates = [c for c in usable if geod.inv(point[1], point[0],
            c['destination']['longitude'], c['destination']['latitude'])[2] <= CANDIDATE_METRES]
        if not candidates:
            return {'status': 'no_candidates', 'minutes': {}}
        status = snap(actor, point)
        if status['status'] != 'snapped':
            counts['cell_snap_failed'] += 1
            return {**status, 'minutes': {}}
        seconds = {}
        for i in range(0, len(candidates), 128):
            seconds.update(matrix(actor, point, candidates[i:i+128]))
        counts['matrix_null_pairs'] += sum(s is None for s in seconds.values())
        minutes = {cid: whole_minutes(s) for cid, s in seconds.items() if s is not None and s <= LIMIT_SECONDS}
        counts['pairs_within90'] += len(minutes)
        return {'status': 'routed', 'minutes': minutes,
                'matrix_null_course_ids': [cid for cid,s in seconds.items() if s is None]}
    try:
        for ix in range(ix0, ix1+1):
            for iy in range(iy0, iy1+1):
                key = (ix, iy)
                lon, lat = to_ll(*centre(key))
                record = cell((lat, lon), key)
                counts['cells_considered'] += 1
                # Sparse table still records failures/nulls. Cells with no nearby
                # courses are defined by the manifest envelope and omitted.
                if record['status'] != 'no_candidates':
                    area = f'{ix//20}_{iy//20}'
                    if area not in handles:
                        handles[area] = (table_dir / (area + '.jsonl')).open('w')
                    handles[area].write(json.dumps({'cell': key, **record}, separators=(',', ':')) + '\n')
                if counts['cells_considered'] % 1000 == 0:
                    write_json(OUT / 'table-progress.json', counts)
                    print(json.dumps(counts), flush=True)
    finally:
        for h in handles.values(): h.close()
    write_json(OUT / 'table-manifest.json', {**counts, 'envelope_cells': [ix0, iy0, ix1, iy1],
        'course_catalog': json.loads((OUT / 'provenance.json').read_text())['catalog'],
        'missing_destination_courses': sum(c['destination'] is None for c in courses),
        'course_snap_failed': sum(c.get('snap',{}).get('status') == 'snap_failed' for c in courses)})
    rng = random.Random(SEED)
    xmin,ymin,xmax,ymax = illinois.bounds
    points = []
    while len(points) < 100:
        x,y = rng.uniform(xmin,xmax),rng.uniform(ymin,ymax)
        if illinois.contains(Point(x,y)):
            lon,lat = to_ll(x,y); points.append((f'random-{len(points):03}',lat,lon))
    all_points = points + representative_points()
    # Look up actual emitted cell entries instead of regenerating them. This also
    # exercises table encoding/decoding and missing-cell semantics.
    wanted = {cell_key(*to_xy(lon,lat)) for _,lat,lon in all_points}
    for path in table_dir.glob('*.jsonl'):
        for line in path.open():
            row=json.loads(line); key=tuple(row['cell'])
            if key in wanted: cache[key]=row
    results=[]
    for name,lat,lon in all_points:
        key=cell_key(*to_xy(lon,lat)); approximate=cache.get(key,{'status':'cell_missing','minutes':{}})
        exact={}; pairs=[]
        origin_snap=snap(actor,(lat,lon))
        for c in usable:
            if geod.inv(lon,lat,c['destination']['longitude'],c['destination']['latitude'])[2] > CANDIDATE_METRES:
                continue
            ref = reference(actor,(lat,lon),c) if origin_snap['status']=='snapped' else {'status':'origin_snap_failed'}
            seconds=ref.get('seconds'); cid=c['course_id']
            status='within_90' if seconds is not None and seconds<=LIMIT_SECONDS else 'over_90' if seconds is not None else ref['status']
            pair={'course_id':cid,'reference_status':status,'reference':ref,'table_minutes':approximate['minutes'].get(cid)}
            if status=='within_90':
                exact[cid]=seconds
                if pair['table_minutes'] is not None:
                    pair['absolute_error_minutes']=abs(pair['table_minutes']-seconds/60)
            pairs.append(pair)
        results.append({'name':name,'latitude':lat,'longitude':lon,'cell':key,
            'origin_snap':origin_snap,'table_status':approximate['status'], 'pairs':pairs,
            'exact_nearest10':nearest10(exact),'table_nearest10':nearest10(approximate['minutes'])})
        write_json(OUT / 'accuracy-origins.json', {'seed':SEED,'distribution':'100 uniform-area EPSG:5070 points inside pinned Illinois polygon, then 12 separate synthetic city/rural/border cases','origins':results})
    sizes={p.name:gzip_file(p) for p in sorted(table_dir.glob('*.jsonl'))}
    write_json(OUT / 'table-sizes.json',{'areas':sizes,'total_raw_bytes':sum(v['raw_bytes'] for v in sizes.values()),'total_gzip_bytes':sum(v['gzip_bytes'] for v in sizes.values())})
    write_json(OUT / 'accuracy.json',{'random100':accuracy_summary(results[:100]),'representative12':accuracy_summary(results[100:])})


if __name__ == '__main__':
    route_pilot()
