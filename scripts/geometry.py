"""State geometry, metric square centres, and reproducible public test origins."""
import json
import math
import random
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform
from common import WORK, SEED, GRID_METRES


def transforms(state='illinois'):
    crs={'alaska':3338,'hawaii':3759}.get(state,5070)
    return (Transformer.from_crs(4326,crs,always_xy=True).transform,
            Transformer.from_crs(crs,4326,always_xy=True).transform,crs)


def cell_key(x,y):
    return math.floor(x/GRID_METRES),math.floor(y/GRID_METRES)


def centre(key):
    return (key[0]+.5)*GRID_METRES,(key[1]+.5)*GRID_METRES


def cells(polygon):
    a,b=cell_key(*polygon.bounds[:2]);c,d=cell_key(*polygon.bounds[2:])
    return [(x,y) for x in range(a,c+1) for y in range(b,d+1)
            if polygon.contains(Point(*centre((x,y))))]


def origins(polygon,to_ll):
    rng=random.Random(SEED);a,b,c,d=polygon.bounds;result=[]
    while len(result)<100:
        x,y=rng.uniform(a,c),rng.uniform(b,d)
        if polygon.contains(Point(x,y)):
            lon,lat=to_ll(x,y)
            result.append({'name':f'random-{len(result):03}','latitude':lat,'longitude':lon})
    return {'seed':SEED,'distribution':'100 uniform-area random origins inside the pinned state polygon; synthetic public coordinates, including water; no private locations','origins':result}
