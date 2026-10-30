"""Download pinned public inputs; preserve catalog names/coordinates verbatim."""
import json
from common import ROOT, WORK, OUT, download, write_json


def select_courses(catalog, state_code='IL'):
    destinations = {d['id']: d for d in catalog['destinations']}
    courses = []
    for c in catalog['courses']:
        if c.get('state') != state_code:
            continue
        # No inference from facility centroids, names or addresses. Missing arrivals stay missing.
        target = destinations.get(c.get('destination_id'))
        point = c.get('map_location')
        # The published browser projection places coordinates on the course
        # map_location, while destinations retain only ID/scope/precision.
        # Preserve those published coordinates and their qualification verbatim.
        if target and point:
            target = {**target, 'latitude': point['latitude'], 'longitude': point['longitude'],
                      'coordinate_source': 'published_course_map_location'}
        else:
            target = None
        courses.append({'course_id': c['course_id'], 'name': c.get('name'),
                        'facility_name': c.get('facility_name'), 'state': c.get('state'),
                        'map_location': c.get('map_location'), 'destination': target,
                        'status': 'target_available' if target else 'arrival_unavailable'})
    return courses


def prepare():
    from shapely.geometry import shape
    from shapely.ops import transform
    from geometry import transforms, origins, geojson_feature
    from common import STATE
    pins=json.loads((ROOT/'pins/sources.json').read_text())
    records=json.loads((ROOT/'pins/extracts.json').read_text())
    shared=ROOT/'work/shared';shared.mkdir(parents=True,exist_ok=True)
    def input_file(spec,name):
        path=shared/name
        if not path.exists():download(spec['url'],path,spec['sha256'],expected_bytes=spec.get('bytes'))
        return path
    features=json.loads(input_file(pins['geofabrik_index'],'regions.json').read_text())['features']
    regions={f['properties']['id'].removeprefix('us/'):f for f in features
             if f['properties']['id'].startswith('us/') and f['properties']['id'].count('/')==1}
    target=regions[STATE];to_xy,to_ll,crs=transforms(STATE)
    polygon=transform(to_xy,shape(target['geometry']))
    write_json(WORK/'state.geojson',geojson_feature(target['geometry']))
    if STATE=='illinois':
        sample=origins(polygon,to_ll,50_000)
        write_json(OUT/'sample-origins.json',sample)
        print('Frozen interior origins: '+json.dumps(sample),flush=True)
    catalog=json.loads(input_file(pins['catalog'],'catalog.json').read_text())
    row=next(r for r in records if r['role']=='state' and r['state']==STATE)
    courses=select_courses(catalog,row['code']);write_json(OUT/'courses.json',courses)
    counts={'state':STATE,'course_records':len(courses),
        'with_published_coordinates':sum(c['destination'] is not None for c in courses),
        'missing_coordinates':sum(c['destination'] is None for c in courses),
        'distinct_destinations':len({c['destination']['id'] for c in courses if c['destination']})}
    write_json(OUT/'catalog-counts.json',counts);print('Catalog: '+json.dumps(counts),flush=True)
    national=[]
    for record in records:
        if record['role']!='state' or record['code'] in {'DC','PR','VI'}:continue
        area=transform(to_xy,shape(regions[record['state']]['geometry'])).area
        national.append({'state':record['state'],'area_m2':area,'input_bytes':record['bytes'],
            'coordinate_courses':len([c for c in select_courses(catalog,record['code']) if c['destination']])})
    write_json(OUT/'national-inputs.json',national)
    source={'state':STATE,'state_code':row['code'],'grid_crs':crs,'buffer_metres':0,
        'extracts':[row],'input_bytes':row['bytes'],'area_m2':polygon.area,
        'scope':'State polygon only, complete ways; no added road buffer. No-buffer routes are not full-network truth.',
        'licenses':pins['licenses'],'catalog':pins['catalog'],'osrm':pins['osrm'],'opentimes':pins['opentimes']}
    write_json(OUT/'sources.json',source);print('Inputs: '+json.dumps(source),flush=True)
    download(row['url'],WORK/'source.osm.pbf',row['md5'],'md5',row['bytes'])
    modo=pins['modo'];builder=shared/'modo_build.py'
    if not builder.exists():download(f"https://raw.githubusercontent.com/adelevski/modo/{modo['commit']}/experiments/browser-routing/build.py",builder,modo['builder_sha256'])
    (WORK/'modo_build.py').write_bytes(builder.read_bytes())


if __name__=='__main__':prepare()
