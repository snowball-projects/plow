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
        if point:
            if target is None:
                target={'id':'course-coordinate:'+c['course_id'],'coordinate_only':True}
            target = {**target, 'latitude': point['latitude'], 'longitude': point['longitude'],
                      'coordinate_source': 'published_course_map_location'}
        else:
            target = None
        courses.append({'course_id': c['course_id'], 'name': c.get('name'),
                        'facility_name': c.get('facility_name'), 'state': c.get('state'),
                        'facility_id':c.get('facility_id'),'published_destination_id':c.get('destination_id'),
                        'map_location': c.get('map_location'), 'destination': target,
                        'status': ('coordinate_only_target' if target.get('coordinate_only') else 'target_available') if target else 'arrival_unavailable'})
    return courses


def catalog_counts(catalog,courses,state_code):
    facilities=[f for f in catalog.get('facilities',[]) if f.get('state')==state_code]
    located=[f for f in facilities if f.get('map_location')]
    linked={c.get('facility_id') for c in catalog['courses'] if c.get('state')==state_code}
    return {'course_records':len(courses),
        'with_published_coordinates':sum(bool(c['map_location']) for c in courses),
        'missing_coordinates':sum(not c['map_location'] for c in courses),
        'selected_routing_records':sum(c['destination'] is not None for c in courses),
        'coordinates_without_destination_id':sum(bool(c['map_location']) and not c['published_destination_id'] for c in courses),
        'coordinate_only_targets':sum(bool(c['destination'] and c['destination'].get('coordinate_only')) for c in courses),
        'distinct_destinations':len({c['destination']['id'] for c in courses if c['destination']}),
        'facilities':len(facilities),'facilities_with_published_coordinates':len(located),
        'coordinate_facilities_without_course_records':sum(f['facility_id'] not in linked for f in located),
        'scope':'Documented course records only; coordinate-bearing facilities without course records are not routing targets. Published map-point qualifications are preserved.'}


def prepare():
    from shapely.geometry import shape
    from shapely.ops import transform
    from geometry import transforms, origins, geojson_feature, state_boundaries
    from common import STATE
    pins=json.loads((ROOT/'pins/sources.json').read_text())
    records=json.loads((ROOT/'pins/extracts.json').read_text())
    shared=ROOT/'work/shared';shared.mkdir(parents=True,exist_ok=True)
    def input_file(spec,name):
        path=shared/name
        if not path.exists():download(spec['url'],path,spec['sha256'],expected_bytes=spec.get('bytes'))
        return path
    regions=state_boundaries(input_file(pins['state_boundaries'],'state-boundaries.zip'))
    row=next(r for r in records if r['role']=='state' and r['state']==STATE)
    target=regions[row['code']];to_xy,to_ll,crs=transforms(STATE)
    polygon=transform(to_xy,shape(target))
    write_json(WORK/'state.geojson',geojson_feature(target))
    if STATE=='illinois':
        # Reproduce the original overnight cohort; all 100 also pass the full
        # administrative-border test. This extent never controls roads or cells.
        features=json.loads(input_file(pins['origin_sampling_extent'],'regions.json').read_text())['features']
        extent=next(f['geometry'] for f in features if f['properties']['id']=='us/illinois')
        sample=origins(transform(to_xy,shape(extent)),to_ll,50_000,polygon)
        write_json(OUT/'sample-origins.json',sample)
        print('Frozen interior origins: '+json.dumps(sample),flush=True)
    catalog=json.loads(input_file(pins['catalog'],'catalog.json').read_text())
    courses=select_courses(catalog,row['code']);write_json(OUT/'courses.json',courses)
    counts={'state':STATE,**catalog_counts(catalog,courses,row['code'])}
    write_json(OUT/'catalog-counts.json',counts);print('Catalog: '+json.dumps(counts),flush=True)
    national=[]
    for record in records:
        if record['role']!='state' or record['code'] in {'DC','PR','VI'}:continue
        area=transform(transforms(record['state'])[0],shape(regions[record['code']])).area
        national.append({'state':record['state'],'area_m2':area,'input_bytes':record['bytes'],
            'coordinate_courses':len([c for c in select_courses(catalog,record['code']) if c['destination']])})
    write_json(OUT/'national-inputs.json',national)
    source={'state':STATE,'state_code':row['code'],'grid_crs':crs,'buffer_metres':0,
        'extracts':[row],'input_bytes':row['bytes'],'area_m2':polygon.area,
        'scope':'Census administrative state polygon only, complete ways; no added road buffer. No-buffer routes are not full-network truth.',
        'state_boundaries':pins['state_boundaries'],
        'destination_scope':'Documented course records with published map coordinates; not every mapped facility',
        'licenses':pins['licenses'],'catalog':pins['catalog'],'osrm':pins['osrm'],'opentimes':pins['opentimes']}
    write_json(OUT/'sources.json',source);print('Inputs: '+json.dumps(source),flush=True)
    download(row['url'],WORK/'source.osm.pbf',row['md5'],'md5',row['bytes'])
    modo=pins['modo'];builder=shared/'modo_build.py'
    if not builder.exists():download(f"https://raw.githubusercontent.com/adelevski/modo/{modo['commit']}/experiments/browser-routing/build.py",builder,modo['builder_sha256'])
    (WORK/'modo_build.py').write_bytes(builder.read_bytes())


if __name__=='__main__':prepare()
