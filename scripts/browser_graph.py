"""Emit the pinned modo experiment's directed full/tiled browser format.

This is a separately named OSM segment/speed profile, not OSRM CH's turn-aware
cost graph. Its size must not be described as a OSRM CH-equivalent browser route
model. No turn restrictions or intersection penalties are represented by v1 CSR.
"""
import hashlib
import importlib.util
import json
import math
import re
import subprocess

from common import WORK, OUT, STATE, write_json, gzip_file

CAR_CLASSES = {'motorway':100, 'motorway_link':50, 'trunk':90, 'trunk_link':45,
               'primary':80, 'primary_link':40, 'secondary':70, 'secondary_link':35,
               'tertiary':60, 'tertiary_link':30, 'unclassified':40,
               'residential':30, 'living_street':10, 'service':20}


def speed(tags, direction):
    text = tags.get('maxspeed:' + direction) or tags.get('maxspeed') or ''
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kph)?\s*', text)
    if match:
        value = float(match[1]) * (1.609344 if match[2] == 'mph' else 1)
        if 0 < value <= 140:
            return value
    return CAR_CLASSES[tags['highway']]


def directions(tags):
    access=next((tags[k] for k in ['motorcar','motor_vehicle','vehicle','access'] if k in tags),None)
    if access in {'no','private'} or tags.get('highway') not in CAR_CLASSES:
        return []
    one=tags.get('oneway', 'yes' if tags.get('junction')=='roundabout' or tags.get('highway')=='motorway' else 'no')
    if one in {'yes','1','true'}: return ['forward']
    if one in {'-1','reverse'}: return ['backward']
    return ['forward','backward']


def build_browser():
    import osmium
    import numpy as np
    from pyproj import Geod
    from scipy.sparse import csr_array
    geod=Geod(ellps='WGS84')
    nodes={}; arcs={}; snap_eligible={}
    class Roads(osmium.SimpleHandler):
        def way(self, way):
            tags={t.k:t.v for t in way.tags}
            allowed=directions(tags)
            if not allowed: return
            for a,b in zip(way.nodes, list(way.nodes)[1:]):
                if not a.location.valid() or not b.location.valid():
                    raise ValueError('Incomplete Illinois highway node reference')
                if a.ref==b.ref: continue
                xy_a=(round(a.lat,7),round(a.lon,7));xy_b=(round(b.lat,7),round(b.lon,7))
                metres=geod.inv(xy_a[1],xy_a[0],xy_b[1],xy_b[0])[2]
                if metres <= 0: continue
                nodes[a.ref]=xy_a;nodes[b.ref]=xy_b
                for direction in allowed:
                    u,v=(a.ref,b.ref) if direction=='forward' else (b.ref,a.ref)
                    seconds=metres/(speed(tags,direction)/3.6)
                    arcs[u,v]=min(arcs.get((u,v),math.inf),seconds)
                    snap_eligible[u,v]=snap_eligible.get((u,v),False) or tags['highway']!='service'
    # flex_mem stores only referenced locations for this Illinois input. The
    # supervisor still enforces the same total RSS/disk/time budget.
    Roads().apply_file(str(WORK/(STATE+'.osm.pbf')),locations=True,idx='flex_mem')
    ids=tuple(sorted(nodes,key=str)); index={value:i for i,value in enumerate(ids)}
    rows=np.fromiter((index[u] for u,v in arcs),dtype=np.int64)
    cols=np.fromiter((index[v] for u,v in arcs),dtype=np.int64)
    weights=np.fromiter(arcs.values(),dtype=np.float64)
    graph=csr_array((weights,(rows,cols)),shape=(len(ids),len(ids)))
    values={'ids':ids,'coordinates':np.rint(np.array([nodes[n] for n in ids])*1e7)/1e7,
            'weights':graph.data,'heads':graph.indices.astype(np.uint32),
            'first_out':graph.indptr.astype(np.uint32)}
    spec=importlib.util.spec_from_file_location('modo_build', WORK/'modo_build.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    values=module.canonicalize(values)
    source=hashlib.file_digest((WORK/(STATE+'.osm.pbf')).open('rb'),'sha256').hexdigest()
    directory=OUT/'browser';directory.mkdir()
    full,full_asset=module.full_payload(values,directory/'full',source)
    tiled,tile_asset=module.tile_payload(values,directory/'tiles',source,0.05)
    # Optional matching sidecar: routing arcs/weights and modo manifests stay intact.
    tails=np.repeat(np.arange(len(values['ids'])),np.diff(values['first_out']))
    mask=np.fromiter((snap_eligible.get((values['ids'][u],values['ids'][v]),False)
                     for u,v in zip(tails,values['heads'])),dtype=np.uint8)
    mask.tofile(directory/'full/snap_eligible.u8')
    totals={}
    for kind in ['full','tiles']:
        sizes={str(p.relative_to(directory/kind)):gzip_file(p) for p in sorted((directory/kind).rglob('*')) if p.is_file()}
        totals[kind]={'files':sizes,'raw_bytes':sum(x['raw_bytes'] for x in sizes.values()),
                      'gzip_bytes':sum(x['gzip_bytes'] for x in sizes.values())}
    write_json(OUT/'browser-sizes.json',{'vertices':len(ids),'arcs':len(weights),'tile_count':len(tiled['tiles']),**totals,
        'profile':'plow-osm-car-segment-static-v1','speed_fallback_kmh':CAR_CLASSES,
        'limitations':'not OSRM CH-equivalent: raw OSM segments, maxspeed or class fallback; no turn restrictions, intersection penalties, conditional access, ferries, or external-border connections; private/no motorcar access excluded',
        'source_sha256':source,'format':'unmodified pinned modo full_payload and tile_payload functions',
        'snap_candidates':'snap_eligible.u8 aligns with full CSR arcs; excludes service roads from matching only; car profile already excludes footpaths',
        'full_manifest_sha256':full_asset['sha256'],'tiles_manifest_sha256':tile_asset['sha256']})

    print('Browser sizes: '+json.dumps({'vertices':len(ids),'arcs':len(weights),'tile_count':len(tiled['tiles']),
        **{kind:{key:totals[kind][key] for key in ['raw_bytes','gzip_bytes']} for kind in ['full','tiles']}}),flush=True)


if __name__=='__main__':
    build_browser()
