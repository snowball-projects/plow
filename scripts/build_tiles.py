"""Build only traffic-free car tiles; merge same-date extracts to deduplicate borders."""
import json
import subprocess
import sys
import time
from common import ROOT, WORK, OUT, write_json


def routing_config(tiles):
    import valhalla
    config = valhalla.get_config(tile_dir=tiles, tile_extract='', verbose=True)
    config['mjolnir'].update({'include_bicycle': False, 'include_pedestrian': False,
                              'import_bike_share_stations': False, 'traffic_extract': '',
                              'admin': '', 'timezone': '', 'max_cache_size': 256_000_000})
    config['mjolnir']['data_processing']['use_admin_db'] = False
    config['loki']['service_defaults'].update({'search_cutoff': 1000, 'minimum_reachability': 0})
    config['service_limits']['auto']['max_matrix_distance'] = 1_000_000
    config['service_limits']['auto']['max_matrix_location_pairs'] = 2500
    config['thor'].update({'clear_reserved_memory': True, 'source_to_target_algorithm': 'timedistancematrix'})
    return config


def build_tiles():
    import valhalla
    extracts = json.loads((ROOT / 'pins/extracts.json').read_text())
    merged = WORK / 'region.osm.pbf'
    merge_started = time.monotonic()
    subprocess.run(['osmium', 'merge', *[str(WORK / (e['state'] + '.osm.pbf')) for e in extracts],
                    '-o', str(merged)], check=True)
    merge_seconds = time.monotonic() - merge_started
    from pyproj import Transformer
    from shapely.geometry import mapping
    from shapely.ops import transform, unary_union
    from routing import polygon_from_poly
    to_xy = Transformer.from_crs(4326, 5070, always_xy=True).transform
    to_ll = Transformer.from_crs(5070, 4326, always_xy=True).transform
    illinois = transform(to_xy, polygon_from_poly(WORK / 'illinois.poly'))
    buffer = illinois.buffer(200_000)
    source_union = unary_union([transform(to_xy, polygon_from_poly(WORK / (e['state'] + '.poly'))) for e in extracts])
    write_json(WORK / 'buffer.geojson', {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {}, 'geometry': mapping(transform(to_ll, buffer))}]})
    clipped = WORK / 'buffer.osm.pbf'
    clip_started = time.monotonic()
    subprocess.run(['osmium', 'extract', '--strategy=complete_ways',
                    '-p', str(WORK / 'buffer.geojson'), str(merged), '-o', str(clipped)], check=True)
    record = {'clipped_pbf_bytes': clipped.stat().st_size, 'merged_pbf_bytes': merged.stat().st_size,
        'merge_seconds': merge_seconds, 'clip_seconds': time.monotonic() - clip_started,
        'states': [e['state'] for e in extracts], 'buffer_metres': 200_000, 'buffer_crs': 'EPSG:5070',
        'buffer_area_km2': buffer.area / 1e6,
        'source_union_buffer_area_share': source_union.intersection(buffer).area / buffer.area,
        'scope': '200 km Illinois buffer intersected with six land-neighbour extracts; Michigan excluded by request; missing source-union areas are not routed; complete ways may extend outside clip'}
    write_json(OUT / 'regional-input.json', record)
    print('Road input: ' + json.dumps(record), flush=True)
    merged.unlink()
    for e in extracts:
        if e['state'] != 'illinois':
            (WORK / (e['state'] + '.osm.pbf')).unlink()
    tiles = WORK / 'tiles'
    tiles.mkdir()
    config = routing_config(tiles)
    write_json(WORK / 'valhalla.json', config)
    write_json(OUT / 'valhalla-config.json', config)
    build_started = time.monotonic()
    subprocess.run([sys.executable, '-m', 'valhalla', 'valhalla_build_tiles',
                    '-c', str(WORK / 'valhalla.json'), '-j', '4', str(clipped)], check=True)
    tile_seconds = time.monotonic() - build_started
    clipped.unlink()
    write_json(OUT / 'tile-build.json', {'tile_bytes': sum(p.stat().st_size for p in tiles.rglob('*') if p.is_file()),
        'tile_files': sum(p.is_file() for p in tiles.rglob('*.gph')), 'threads': 4, 'tile_build_seconds': tile_seconds,
        'traffic': 'none: no traffic extract, no predicted/live inputs, no date_time',
        'elevation': 'not built', 'admin_timezone': 'not built; traffic-free car pilot only'})
    print('Tile build: ' + (OUT / 'tile-build.json').read_text(), flush=True)


if __name__ == '__main__':
    build_tiles()
