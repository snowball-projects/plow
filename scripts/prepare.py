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
    import os
    from shapely.geometry import shape, mapping
    from shapely.ops import transform, unary_union
    from geometry import transforms, origins, geojson_feature
    state=os.environ.get('PLOW_STATE','illinois')
    pins=json.loads((ROOT/'pins/sources.json').read_text())
    records=json.loads((ROOT/'pins/extracts.json').read_text())
    index=pins['geofabrik_index']
    download(index['url'],WORK/'regions.json',index['sha256'])
    features=json.loads((WORK/'regions.json').read_text())['features']
    regions={(f['properties']['id'].split('/')[1] if f['properties']['id'].startswith('us/') else f['properties']['id']):f for f in features
             if f['properties']['id'].startswith('us/') and f['properties']['id'].count('/')==1 or f['properties']['id'].startswith('us-')}
    target=regions[state]; to_xy,to_ll,crs=transforms(state)
    polygon=transform(to_xy,shape(target['geometry']));buffer=polygon.buffer(300_000)
    selected=[r for r in records if r['role']=='region' and transform(to_xy,shape(regions[r['state']]['geometry'])).intersects(buffer)]
    union=unary_union([transform(to_xy,shape(regions[r['state']]['geometry'])) for r in selected])
    write_json(WORK/'state.geojson',geojson_feature(target['geometry']))
    write_json(WORK/'buffer.geojson',geojson_feature(mapping(transform(to_ll,buffer))))
    write_json(OUT/'sample-origins.json',origins(polygon,to_ll))
    cat=pins['catalog'];download(cat['url'],WORK/'catalog.json',cat['sha256'],expected_bytes=cat['bytes'])
    catalog=json.loads((WORK/'catalog.json').read_text())
    # Metadata-only national planning proxies; no other state roads are built.
    projected={r['state']:transform(to_xy,shape(regions[r['state']]['geometry'])) for r in records}
    national=[r for r in records if r['role']=='state' and r['code'] not in {'DC','PR','VI'}]
    course_counts={r['code']:sum(c.get('state')==r['code'] and c.get('map_location') is not None and c.get('destination_id') is not None for c in catalog['courses']) for r in national}
    proxies={}
    for r in national:
        region=projected[r['state']];region_buffer=region.buffer(300000)
        volume=sum(v['bytes']*projected[v['state']].intersection(region_buffer).area/projected[v['state']].area for v in records if v['role']=='region')
        proxies[r['state']]={'volume':volume,'pairs':region.area/4e6*course_counts[r['code']],'courses':course_counts[r['code']],'raw':r['bytes']}
    baseline=proxies[state];ratios={key:[v[key]/baseline[key] for v in proxies.values()] for key in ['volume','pairs','courses','raw']}
    write_json(OUT/'estimate-inputs.json',{'buffer_volume_ratio':sum(ratios['volume']),'largest_buffer_ratio':max(ratios['volume']),
        'pair_ratio':sum(ratios['pairs']),'largest_pair_ratio':max(ratios['pairs']),
        'course_ratio':sum(ratios['courses']),'largest_course_ratio':max(ratios['courses']),
        'raw_input_ratio':sum(ratios['raw']),'largest_raw_ratio':max(ratios['raw']),
        'qualification':'Metadata-only hypothesis: area-weighted whole-OSM compressed bytes approximate buffered road work; area/4km2 x public-coordinate courses approximate matrix pairs; EPSG distortion, snap/retention fractions, topology and CH nonlinearity unmeasured outside Illinois; no national execution'})
    code=target['properties']['iso3166-2'][0][3:]
    courses=select_courses(catalog,code);write_json(OUT/'courses.json',courses)
    counts={'state':state,'course_records':len(courses),
        'with_published_coordinates':sum(c['destination'] is not None for c in courses),
        'missing_coordinates':sum(c['destination'] is None for c in courses),
        'distinct_destinations':len({c['destination']['id'] for c in courses if c['destination']}),
        'national_course_records':len(catalog['courses'])}
    write_json(OUT/'catalog-counts.json',counts);print('Catalog: '+json.dumps(counts),flush=True)
    source={'state':state,'state_code':code,'grid_crs':crs,'buffer_metres':300000,
        'source_union_buffer_area_share':union.intersection(buffer).area/buffer.area,
        'extracts':selected,'input_bytes':sum(r['bytes'] for r in selected),
        'scope':'Complete 300 km Illinois buffer from intersecting Geofabrik regional extracts; polygon-area coverage is not independent route-quality validation',
        'licenses':pins['licenses'],'catalog':cat,'osrm':pins['osrm'],'opentimes':pins['opentimes']}
    write_json(OUT/'sources.json',source);print('Inputs: '+json.dumps(source),flush=True)
    for row in selected:
        download(row['url'],WORK/(row['state']+'.osm.pbf'),row['md5'],'md5',row['bytes'])
        print('Downloaded '+row['state']+' '+str(row['bytes'])+' bytes',flush=True)
    modo=pins['modo'];download(f"https://raw.githubusercontent.com/adelevski/modo/{modo['commit']}/experiments/browser-routing/build.py",WORK/'modo_build.py',modo['builder_sha256'])


if __name__=='__main__': prepare()
