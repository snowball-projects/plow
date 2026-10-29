"""Download pinned public inputs; preserve catalog names/coordinates verbatim."""
import json
import subprocess
from common import ROOT, WORK, OUT, download, write_json


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
    destinations = {d['id']: d for d in catalog['destinations']}
    courses = []
    for c in catalog['courses']:
        if c.get('state') != 'IL':
            continue
        # No inference from facility centroids, names or addresses. Missing arrivals stay missing.
        target = destinations.get(c.get('destination_id'))
        courses.append({'course_id': c['course_id'], 'name': c.get('name'),
                        'facility_name': c.get('facility_name'), 'state': c.get('state'),
                        'map_location': c.get('map_location'), 'destination': target,
                        'status': 'target_available' if target else 'arrival_unavailable'})
    write_json(OUT / 'courses.json', courses)
    write_json(OUT / 'catalog-counts.json', {'illinois_courses': len(courses),
               'with_destination': sum(c['destination'] is not None for c in courses),
               'missing_destination': sum(c['destination'] is None for c in courses),
               'nationwide_courses': len(catalog['courses']), 'catalog_download': downloaded})
    print((OUT / 'catalog-counts.json').read_text(), flush=True)
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
    # Geofabrik's current .poly is content-pinned separately; never accept silent movement.
    boundary = pins['boundary']
    download(boundary['url'], WORK / 'illinois.poly', boundary['sha256'])


if __name__ == '__main__':
    prepare()
