"""State geometry, metric square centres, and reproducible public test origins."""
import json
import math
import random
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform
from common import WORK, SEED, GRID_METRES


def geojson_feature(geometry):
    # Ubuntu's packaged osmium requires a Feature rather than bare geometry.
    return {'type':'Feature','properties':{},'geometry':geometry}


def transforms(state='illinois'):
    crs={'alaska':3338,'hawaii':3759}.get(state,5070)
    return (Transformer.from_crs(4326,crs,always_xy=True).transform,
            Transformer.from_crs(crs,4326,always_xy=True).transform,crs)


def cell_key(x,y,spacing=GRID_METRES):
    return math.floor(x/spacing),math.floor(y/spacing)


def centre(key,spacing=GRID_METRES):
    return (key[0]+.5)*spacing,(key[1]+.5)*spacing


def cells(polygon,spacing=GRID_METRES):
    a,b=cell_key(*polygon.bounds[:2],spacing);c,d=cell_key(*polygon.bounds[2:],spacing)
    return [(x,y) for x in range(a,c+1) for y in range(b,d+1)
            if polygon.contains(Point(*centre((x,y),spacing)))]


def origins(polygon,to_ll,inset_metres=0):
    sampling=polygon.buffer(-inset_metres) if inset_metres else polygon
    rng=random.Random(SEED);a,b,c,d=sampling.bounds;result=[];distances=[]
    while len(result)<100:
        x,y=rng.uniform(a,c),rng.uniform(b,d)
        if sampling.contains(Point(x,y)):
            lon,lat=to_ll(x,y)
            result.append({'name':f'random-{len(result):03}','latitude':lat,'longitude':lon})
            distances.append(polygon.boundary.distance(Point(x,y)))
    return {'seed':SEED,'inset_metres':inset_metres,'minimum_border_distance_metres':min(distances),
            'distribution':f'100 uniform-area random origins in the pinned state polygon eroded by {inset_metres} metric metres; no resampling after routing failures',
            'origins':result}
