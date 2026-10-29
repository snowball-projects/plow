"""Download pinned public inputs; preserve catalog names/coordinates verbatim."""
import json
import subprocess
from common import ROOT, WORK, OUT, download, write_json


def select_courses(catalog):
    destinations = {d['id']: d for d in catalog['destinations']}
    courses = []
    for c in catalog['courses']:
        if c.get('state') != 'IL':
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
    pins = json.loads((ROOT / 'pins/sources.json').read_text())
    extracts = json.loads((ROOT / 'pins/extracts.json').read_text())
    # Minimal provenance is available even when the first download fails.
    write_json(OUT / 'provenance.json', {**pins, 'extracts': extracts})
    cat = pins['catalog']
    downloaded = download(cat['url'], WORK / 'catalog.json', cat['sha256'], expected_bytes=cat['bytes'])
    catalog = json.loads((WORK / 'catalog.json').read_text())
    if catalog['revision'] != cat['revision'] or catalog['catalog_release_key'] != cat['release_key']:
        raise ValueError('Catalog release identity changed')
    courses = select_courses(catalog)
    write_json(OUT / 'courses.json', courses)
    write_json(OUT / 'catalog-counts.json', {'illinois_courses': len(courses),
               'with_destination': sum(c['destination'] is not None for c in courses),
               'missing_destination': sum(c['destination'] is None for c in courses),
               'nationwide_courses': len(catalog['courses']), 'catalog_download': downloaded})
    print((OUT / 'catalog-counts.json').read_text(), flush=True)
    boundary = pins['boundary']
    download(boundary['url'], WORK / 'illinois.poly', boundary['sha256'])
    from routing import sample_origins
    write_json(OUT / 'sample-origins.json', sample_origins())
    records = []
    for e in extracts:
        path = WORK / (e['state'] + '.osm.pbf')
        row = download(e['url'], path, e['md5'], 'md5', e['bytes'])
        header = json.loads(subprocess.check_output(['osmium', 'fileinfo', '-j', str(path)]))
        records.append({**e, **row, 'pbf_fileinfo': header})
        write_json(OUT / 'downloaded-extracts.json', records)
        print(f"Downloaded {e['state']}: {row['bytes']} bytes", flush=True)
    modo = pins['modo']
    download(f"https://raw.githubusercontent.com/adelevski/modo/{modo['commit']}/experiments/browser-routing/build.py",
             WORK / 'modo_build.py', modo['builder_sha256'])



if __name__ == '__main__':
    prepare()
