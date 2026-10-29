"""Build only traffic-free car tiles; merge same-date extracts to deduplicate borders."""
import json
import subprocess
import sys
import time
from pathlib import Path
from common import ROOT, WORK, OUT, write_json


def build_tiles():
    import valhalla
    extracts = json.loads((ROOT / 'pins/extracts.json').read_text())
    merged = WORK / 'region.osm.pbf'
    merge_started = time.monotonic()
    subprocess.run(['osmium', 'merge', *[str(WORK / (e['state'] + '.osm.pbf')) for e in extracts],
                    '-o', str(merged)], check=True)
    write_json(OUT / 'regional-input.json', {'merged_pbf_bytes': merged.stat().st_size,
        'merge_seconds': time.monotonic() - merge_started, 'states': [e['state'] for e in extracts],
        'scope': 'seven-state deduplicated regional tile input; distinct from Illinois-only course/table scope'})
    # Keep only Illinois for the separate browser graph; release other raw inputs after merge.
    for e in extracts:
        if e['state'] != 'illinois':
            (WORK / (e['state'] + '.osm.pbf')).unlink()
    tiles = WORK / 'tiles'
    tiles.mkdir()
    config = valhalla.get_config(tile_dir=tiles, tile_extract='', verbose=True)
    config['mjolnir'].update({'include_bicycle': False, 'include_pedestrian': False,
                              'import_bike_share_stations': False, 'traffic_extract': '',
                              'admin': '', 'timezone': '', 'max_cache_size': 256_000_000})
    config['mjolnir']['data_processing']['use_admin_db'] = False
    config['loki']['service_defaults'].update({'search_cutoff': 1000, 'minimum_reachability': 0})
    config['service_limits']['auto']['max_matrix_distance'] = 600_000
    config['service_limits']['auto']['max_matrix_location_pairs'] = 2500
    config['thor']['clear_reserved_memory'] = True
    write_json(WORK / 'valhalla.json', config)
    write_json(OUT / 'valhalla-config.json', config)
    subprocess.run([sys.executable, '-m', 'valhalla', 'valhalla_build_tiles',
                    '-c', str(WORK / 'valhalla.json'), '-j', '2', str(merged)], check=True)
    merged.unlink()
    write_json(OUT / 'tile-build.json', {'tile_bytes': sum(p.stat().st_size for p in tiles.rglob('*') if p.is_file()),
        'tile_files': sum(p.is_file() for p in tiles.rglob('*.gph')), 'threads': 2,
        'traffic': 'none: no traffic extract, no predicted/live inputs, no date_time',
        'elevation': 'not built', 'admin_timezone': 'not built; traffic-free car pilot only'})


if __name__ == '__main__':
    build_tiles()
