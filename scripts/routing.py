"""2 km cells and reproducible point-reference comparison over the named graph."""
import json
import math
import random
import time

from common import (WORK, OUT, SEED, GRID_METRES, LIMIT_SECONDS,
                    write_json, whole_minutes, nearest10, gzip_file)

# This upper bound is deliberately generous: top_speed=140 km/h means at most
# 210 km driving in 90 min, plus <=1 km snap at each endpoint. EPSG:5070's
# candidates use geodesic <=212 km.
CANDIDATE_METRES = 212_000
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


BATCH_SIZE = 2500


def matrix_times(actor, sources, targets):
    """Directed source -> target durations, with the requested expansion verified."""
    result = decode(actor.matrix({**request_base(), 'sources': [location(p) for p in sources],
                                 'targets': [location(p) for p in targets]}))
    if result.get('algorithm') != 'timedistancematrix':
        raise ValueError('Expected timedistancematrix, got ' + str(result.get('algorithm')))
    rows = result['sources_to_targets']
    if len(rows) != len(sources) or any(len(row) != len(targets) for row in rows):
        raise ValueError('Matrix dimensions mismatch')
    times = [[pair.get('time') for pair in row] for row in rows]
    if any(t is not None and (not math.isfinite(t) or t < 0) for row in times for t in row):
        raise ValueError('Invalid matrix duration')
    return times


def course_matrix(actor, cells, course):
    # More than one source and one target makes TimeDistanceMatrix expand in
    # reverse from the course; 1x1 tails remain direction-correct.
    target = (course['destination']['latitude'], course['destination']['longitude'])
    return [row[0] for row in matrix_times(actor, cells, [target])]


def origin_matrices(actor, origins, targets):
    """One attempted one-to-many call per origin, including failed origins."""
    from valhalla import ValhallaError
    for origin in origins:
        failure = None
        try:
            seconds = matrix_times(actor, [(origin['latitude'], origin['longitude'])], targets)[0]
        except ValhallaError as error:
            seconds = [None] * len(targets)
            failure = {'status':'matrix_error', 'valhalla_code':error.code}
        yield origin, seconds, failure


def illinois_cells(polygon):
    """Only centres within the metric polygon, not its bounding rectangle."""
    from shapely.geometry import Point
    ix0, iy0 = cell_key(*polygon.bounds[:2])
    ix1, iy1 = cell_key(*polygon.bounds[2:])
    return [(ix, iy) for ix in range(ix0, ix1+1) for iy in range(iy0, iy1+1)
            if polygon.contains(Point(*centre((ix, iy))))]


def representative_points():
    # Public city centres, synthetic origins, never user locations.
    return [('chicago', 41.8818, -87.6232), ('rockford', 42.2711, -89.0940),
            ('quad-cities-border', 41.5067, -90.5151), ('quincy-border', 39.9356, -91.4099),
            ('east-st-louis-border', 38.6245, -90.1509), ('cairo-border', 37.0053, -89.1765),
            ('danville-border', 40.1245, -87.6300), ('springfield', 39.7817, -89.6501),
            ('champaign', 40.1164, -88.2434), ('peoria', 40.6936, -89.5890),
            ('effingham-rural-region', 39.1200, -88.5434), ('carbondale', 37.7273, -89.2168)]


def sample_origins():
    from pyproj import Transformer
    from shapely.ops import transform
    from shapely.geometry import Point
    to_xy = Transformer.from_crs(4326, 5070, always_xy=True).transform
    to_ll = Transformer.from_crs(5070, 4326, always_xy=True).transform
    illinois = transform(to_xy, polygon_from_poly(WORK / 'illinois.poly'))
    rng = random.Random(SEED)
    xmin,ymin,xmax,ymax = illinois.bounds
    points = []
    while len(points) < 100:
        x,y = rng.uniform(xmin,xmax),rng.uniform(ymin,ymax)
        if illinois.contains(Point(x,y)):
            lon,lat = to_ll(x,y); points.append((f'random-{len(points):03}',lat,lon))
    points += representative_points()
    return {'seed': SEED, 'distribution': '100 uniform-area EPSG:5070 origins within pinned Illinois polygon, plus 12 separate synthetic representative city/rural/border points',
            'origins': [{'name':name,'latitude':lat,'longitude':lon} for name,lat,lon in points]}


def accuracy_summary(rows):
    eligible_pairs = [p for r in rows for p in r['pairs'] if p['reference_status'] == 'within_90']
    one = sum(p.get('absolute_error_minutes', math.inf) <= 1 for p in eligible_pairs)
    two = sum(p.get('absolute_error_minutes', math.inf) <= 2 for p in eligible_pairs)
    top_eligible = [r for r in rows if len(r['exact_nearest10']) == 10]
    ordered = sum(r['exact_nearest10'] == r['table_nearest10'] for r in top_eligible)
    return {'origins': len(rows), 'exact_within_90_pairs': len(eligible_pairs),
            'origin_snap_failed': sum(r.get('origin_snap', {}).get('status') == 'snap_failed' for r in rows),
            'origin_snap_succeeded': sum(r.get('origin_snap', {}).get('status') == 'snapped' for r in rows),
            'origins_with_exact_within90': sum(any(p['reference_status'] == 'within_90' for p in r['pairs']) for r in rows),
            'origins_without_exact_within90': sum(not any(p['reference_status'] == 'within_90' for p in r['pairs']) for r in rows),
            'candidate_origin_course_pairs': sum(len(r['pairs']) for r in rows),
            'successful_reference_pairs': sum(p['reference_status'] in {'within_90', 'over_90'} for r in rows for p in r['pairs']),
            'over90_reference_pairs': sum(p['reference_status'] == 'over_90' for r in rows for p in r['pairs']),
            'origin_snap_failed_pairs': sum(p['reference_status'] == 'origin_snap_failed' for r in rows for p in r['pairs']),
            'courses_with_exact_within90': len({p['course_id'] for p in eligible_pairs if 'course_id' in p}),
            'table_snap_failed_origins': sum(r.get('table_status') == 'snap_failed' for r in rows),
            'table_missing_cell_origins': sum(r.get('table_status') == 'cell_missing' for r in rows),
            'within_1_minute_count': one, 'within_2_minutes_count': two,
            'within_1_minute_share': one / len(eligible_pairs) if eligible_pairs else None,
            'within_2_minutes_share': two / len(eligible_pairs) if eligible_pairs else None,
            'nearest10_eligible_origins': len(top_eligible), 'nearest10_same_order_count': ordered,
            'nearest10_same_order_share': ordered / len(top_eligible) if top_eligible else None,
            'missing_table_for_exact_within90': sum(p.get('table_minutes') is None for p in eligible_pairs),
            'matrix_error_pairs': sum(p['reference_status'] == 'matrix_error' for r in rows for p in r['pairs']),
            'matrix_null_pairs': sum(p['reference_status'] == 'matrix_null' for r in rows for p in r['pairs']),
            'nearest10_definition': 'Exact point-to-course seconds then course_id vs encoded cell-centre minutes then course_id; top-10 ID sequences identical, exact >=10 within90 denominator; missing/short table fails',
            'error_definition': 'abs(encoded whole-minute centre time - exact point matrix seconds/60); exact <=5400s pairs denominator, missing centre entry fails both thresholds'}


def route_pilot():
    import valhalla
    from pyproj import Transformer, Geod
    to_xy = Transformer.from_crs(4326, 5070, always_xy=True).transform
    to_ll = Transformer.from_crs(5070, 4326, always_xy=True).transform
    geod = Geod(ellps='WGS84')
    actor = valhalla.Actor(str(WORK / 'valhalla.json'))
    courses = json.loads((OUT / 'courses.json').read_text())
    usable = []
    for c in courses:
        if c['destination']:
            c['snap'] = snap(actor, (c['destination']['latitude'], c['destination']['longitude']))
            if c['snap']['status'] == 'snapped':
                usable.append(c)
    write_json(OUT / 'courses.json', courses)
    coverage = {'course_records': len(courses),
        'with_published_coordinates': sum(c['map_location'] is not None for c in courses),
        'missing_coordinates': sum(c['map_location'] is None for c in courses),
        'distinct_published_destination_ids': len({c['destination']['id'] for c in courses if c['destination']}),
        'snapped_course_records': len(usable),
        'distinct_snapped_destination_ids': len({c['destination']['id'] for c in usable}),
        'failed_course_snap_records': sum(c.get('snap', {}).get('status') == 'snap_failed' for c in courses),
        'access_filter': 'none; all Illinois course records retained'}
    write_json(OUT / 'course-coverage.json', coverage)
    print('Course coverage: '+json.dumps(coverage), flush=True)
    if not usable:
        raise RuntimeError('No published Illinois destination can be snapped within 1 km')
    from shapely.ops import transform
    illinois = transform(to_xy, polygon_from_poly(WORK / 'illinois.poly'))
    keys = illinois_cells(illinois)
    points = {}
    table = {}
    table_started = time.monotonic()
    counts = {'cells_considered': len(keys), 'cell_snap_failed': 0,
              'candidate_pairs_considered': 0, 'matrix_pairs_requested': 0,
              'matrix_null_pairs': 0, 'matrix_error_pairs': 0, 'matrix_error_batches': 0,
              'pairs_within90': 0, 'matrix_calls_planned': 0, 'matrix_calls_completed': 0,
              'courses_completed': 0, 'batch_size': BATCH_SIZE,
              'grid_crs': 'EPSG:5070', 'cell_metres': GRID_METRES,
              'candidate_radius_metres': CANDIDATE_METRES,
              'rounding': 'floor(seconds/60+0.5), raw <=5400 eligibility',
              'area_partition': '20x20 cells = 40 km squares',
              'scope': 'centres strictly inside pinned Illinois polygon; roads in 200 km buffer intersected with six source extracts',
              'snap_policy': 'drivable edge within 1000 m; failures explicit, no invented durations',
              'algorithm': 'timedistancematrix; sources=cell centres, single target=course'}
    for key in keys:
        lon, lat = to_ll(*centre(key))
        status = snap(actor, (lat, lon))
        table[key] = {**status, 'minutes': {}, 'matrix_null_course_ids': [], 'matrix_error_course_ids': []}
        if status['status'] == 'snapped':
            points[key] = (lat, lon)
        else:
            counts['cell_snap_failed'] += 1
    plans = []
    for course in usable:
        dest = course['destination']
        candidates = [key for key, (lat, lon) in points.items()
                      if geod.inv(lon, lat, dest['longitude'], dest['latitude'])[2] <= CANDIDATE_METRES]
        plans.append((course, candidates))
        counts['candidate_pairs_considered'] += len(candidates)
        counts['matrix_calls_planned'] += math.ceil(len(candidates) / BATCH_SIZE)
    print('Table plan: ' + json.dumps(counts), flush=True)
    from valhalla import ValhallaError
    for course, candidates in plans:
        cid = course['course_id']
        for start in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[start:start+BATCH_SIZE]
            counts['matrix_pairs_requested'] += len(batch)
            try:
                seconds = course_matrix(actor, [points[key] for key in batch], course)
            except ValhallaError as error:
                counts['matrix_error_batches'] += 1
                counts['matrix_error_pairs'] += len(batch)
                for key in batch: table[key]['matrix_error_course_ids'].append(cid)
                print(f'Matrix batch error course={cid} code={error.code} cells={len(batch)}', flush=True)
            else:
                for key, duration in zip(batch, seconds):
                    if duration is None:
                        counts['matrix_null_pairs'] += 1
                        table[key]['matrix_null_course_ids'].append(cid)
                    elif duration <= LIMIT_SECONDS:
                        table[key]['minutes'][cid] = whole_minutes(duration)
                        counts['pairs_within90'] += 1
            counts['matrix_calls_completed'] += 1
            counts['table_generation_seconds'] = time.monotonic() - table_started
            write_json(OUT / 'table-progress.json', counts)
            print('Table progress: ' + json.dumps(counts), flush=True)
        counts['courses_completed'] += 1
    table_dir = OUT / 'tables'; table_dir.mkdir()
    handles = {}
    try:
        for key, record in table.items():
            area = f'{key[0]//20}_{key[1]//20}'
            if area not in handles: handles[area] = (table_dir / (area + '.jsonl')).open('w')
            handles[area].write(json.dumps({'cell':key, **record}, separators=(',', ':')) + '\n')
    finally:
        for handle in handles.values(): handle.close()
    counts['table_generation_seconds'] = time.monotonic() - table_started
    write_json(OUT / 'table-manifest.json', counts)
    print('Table complete: ' + json.dumps(counts), flush=True)
    # Decode emitted rows for the sampled cells so validation covers encoding.
    samples = json.loads((OUT / 'sample-origins.json').read_text())
    wanted = {cell_key(*to_xy(r['longitude'], r['latitude'])) for r in samples['origins']}
    cache = {}
    for path in table_dir.glob('*.jsonl'):
        for line in path.open():
            row = json.loads(line); key = tuple(row['cell'])
            if key in wanted: cache[key] = row
    accuracy_started = time.monotonic()
    results = []
    targets = [(c['destination']['latitude'], c['destination']['longitude']) for c in usable]
    for origin, seconds, failure in origin_matrices(actor, samples['origins'], targets):
        name, lat, lon = origin['name'], origin['latitude'], origin['longitude']
        key = cell_key(*to_xy(lon, lat))
        approximate = cache.get(key, {'status':'cell_missing','minutes':{}})
        origin_snap = snap(actor, (lat, lon))
        exact = {}; pairs = []
        for course, duration in zip(usable, seconds):
            cid = course['course_id']
            status = ('within_90' if duration <= LIMIT_SECONDS else 'over_90') if duration is not None else (
                'origin_snap_failed' if origin_snap['status'] == 'snap_failed' else 'matrix_error' if failure else 'matrix_null')
            pair = {'course_id':cid, 'reference_status':status, 'table_minutes':approximate['minutes'].get(cid),
                    'reference_seconds':duration}
            if status == 'within_90':
                exact[cid] = duration
                if pair['table_minutes'] is not None: pair['absolute_error_minutes'] = abs(pair['table_minutes'] - duration/60)
            pairs.append(pair)
        results.append({**origin, 'cell':key, 'origin_snap':origin_snap,
            'table_status':approximate['status'], 'pairs':pairs, 'matrix_failure':failure,
            'exact_nearest10':nearest10(exact), 'table_nearest10':nearest10(approximate['minutes'])})
        print(f'Accuracy matrix calls: {len(results)}/{len(samples["origins"])} origin={name} failure={failure}', flush=True)
    sizes = {p.name:gzip_file(p) for p in sorted(table_dir.glob('*.jsonl'))}
    write_json(OUT / 'table-sizes.json', {'areas':sizes,'total_raw_bytes':sum(v['raw_bytes'] for v in sizes.values()),
                                        'total_gzip_bytes':sum(v['gzip_bytes'] for v in sizes.values())})
    write_json(OUT / 'accuracy-origins.json', {**samples, 'origins':results})
    accuracy = {'random100':accuracy_summary(results[:100]), 'representative12':accuracy_summary(results[100:]),
                'matrix_calls':len(results), 'accuracy_seconds':time.monotonic()-accuracy_started,
                'qualification':'matrix-to-matrix cell discretization/whole-minute encoding accuracy, not independent route realism; TimeDistanceMatrix does not enforce destination-only/no-thru like CostMatrix'}
    write_json(OUT / 'accuracy.json', accuracy)
    print('Accuracy complete: ' + json.dumps(accuracy), flush=True)
    print('Table sizes: ' + (OUT / 'table-sizes.json').read_text(), flush=True)


if __name__ == '__main__':
    route_pilot()
