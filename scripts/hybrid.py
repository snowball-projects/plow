"""Ten-minute searches on the exported modo CSR, composed with stored cell times."""
import heapq
import json
import math
import time

import numpy as np
from shapely import STRtree, linestrings, points, line_locate_point

from common import OUT, STATE, LIMIT_SECONDS, write_json, nearest10
from geometry import transforms, centre
from routing import accuracy_summary

LOCAL_SECONDS=600


class Graph:
    def __init__(self, first_out, heads, weights, xy):
        self.first_out,self.heads,self.weights=first_out,heads,weights
        n=len(first_out)-1
        tails=np.repeat(np.arange(n,dtype=np.uint32),np.diff(first_out))
        low=np.minimum(tails,heads);high=np.maximum(tails,heads)
        keys,inverse=np.unique(low.astype(np.uint64)*n+high,return_inverse=True)
        self.u=(keys//n).astype(np.uint32);self.v=(keys%n).astype(np.uint32)
        self.forward=np.full(len(keys),np.inf);self.reverse=np.full(len(keys),np.inf)
        mask=tails==low
        np.minimum.at(self.forward,inverse[mask],weights[mask])
        np.minimum.at(self.reverse,inverse[~mask],weights[~mask])
        self.lines=linestrings(np.stack([xy[self.u],xy[self.v]],axis=1))
        self.tree=STRtree(self.lines)

    @classmethod
    def load(cls, directory):
        manifest=json.loads((directory/'manifest.json').read_text())
        def array(name):
            value=manifest['arrays'][name]
            return np.memmap(directory/value['url'],dtype=value['dtype'],mode='r',shape=(value['length'],))
        coordinates=array('coordinates_e7').reshape(-1,2)/1e7
        to_xy,_,_=transforms(STATE)
        xy=np.column_stack(to_xy(coordinates[:,1],coordinates[:,0]))
        return cls(array('first_out'),array('heads'),array('weights'),xy)

    def snap(self, xy):
        """Nearest segment within 1 km, ties by canonical endpoint indices."""
        locations=points(np.asarray(xy))
        indices,distances=self.tree.query_nearest(locations,max_distance=1000,return_distance=True)
        result=[None]*len(locations)
        # query_nearest returns all equidistant segments; choose consistently.
        order=np.lexsort((indices[1],distances,indices[0]))
        for index in order:
            point,segment=map(int,indices[:,index])
            if result[point] is None:
                fraction=float(line_locate_point(self.lines[segment],locations[point],normalized=True))
                result[point]=(segment,min(1,max(0,fraction)),float(distances[index]))
        return result

    def endpoint_costs(self, snap, departing):
        segment,fraction,_=snap;u=int(self.u[segment]);v=int(self.v[segment])
        forward=float(self.forward[segment]);reverse=float(self.reverse[segment])
        # At an endpoint, all incident CSR arcs remain usable, even when this
        # particular segment permits travel only toward that endpoint.
        if departing:
            return [(u,0 if fraction==0 else fraction*reverse),
                    (v,0 if fraction==1 else (1-fraction)*forward)]
        return [(u,0 if fraction==0 else fraction*forward),
                (v,0 if fraction==1 else (1-fraction)*reverse)]

    def reached_cells(self, origin, targets, budget=LOCAL_SECONDS):
        if origin is None:return {}
        reached={};distance={};queue=[]
        def reach(cell,seconds):
            if seconds<=budget and seconds<reached.get(cell,math.inf):reached[cell]=seconds
        # A target on the starting segment can precede the first settled vertex.
        segment,fraction,_=origin
        for cell,target_fraction in targets.by_segment.get(segment,[]):
            delta=target_fraction-fraction
            cost=0 if delta==0 else delta*self.forward[segment] if delta>0 else -delta*self.reverse[segment]
            reach(cell,float(cost))
        for vertex,seconds in self.endpoint_costs(origin,True):
            if seconds<=budget and seconds<distance.get(vertex,math.inf):
                distance[vertex]=seconds;heapq.heappush(queue,(seconds,vertex))
        while queue:
            seconds,vertex=heapq.heappop(queue)
            if seconds!=distance[vertex]:continue
            for cell,partial in targets.by_vertex.get(vertex,[]):reach(cell,seconds+partial)
            for arc in range(int(self.first_out[vertex]),int(self.first_out[vertex+1])):
                head=int(self.heads[arc]);candidate=seconds+float(self.weights[arc])
                if candidate<=budget and candidate<distance.get(head,math.inf):
                    distance[head]=candidate;heapq.heappush(queue,(candidate,head))
        return reached


class Targets:
    def __init__(self, graph, snaps):
        self.by_vertex={};self.by_segment={}
        for cell,snap in enumerate(snaps):
            if snap is None:continue
            segment,fraction,_=snap
            self.by_segment.setdefault(segment,[]).append((cell,fraction))
            for vertex,seconds in graph.endpoint_costs(snap,False):
                if math.isfinite(seconds):self.by_vertex.setdefault(vertex,[]).append((cell,seconds))


def compose(reached, tables):
    estimates={}
    for cell,local_seconds in reached.items():
        for course,minutes in tables[cell]['minutes'].items():
            seconds=local_seconds+60*minutes
            estimates[course]=min(estimates.get(course,math.inf),seconds)
    return estimates


def main():
    began=time.monotonic();graph=Graph.load(OUT/'browser/full')
    tables=[json.loads(line) for path in sorted((OUT/'tables').glob('*.jsonl')) for line in path.open()]
    snaps=graph.snap([centre(row['cell']) for row in tables]);targets=Targets(graph,snaps)
    references=json.loads((OUT/'accuracy-origins.json').read_text())
    setup_seconds=time.monotonic()-began;results=[];to_xy,_,_=transforms(STATE)
    for reference in references['origins']:
        started=time.monotonic()
        snap=graph.snap([to_xy(reference['longitude'],reference['latitude'])])[0]
        reached=graph.reached_cells(snap,targets);estimates=compose(reached,tables)
        elapsed=time.monotonic()-started
        pairs=[]
        for original in reference['pairs']:
            pair={**original,'table_minutes':estimates.get(original['course_id'])}
            pair.pop('absolute_error_minutes',None)
            if pair['table_minutes'] is not None:
                pair['table_minutes']/=60
                if pair['reference_status']=='within_90':
                    pair['absolute_error_minutes']=abs(pair['table_minutes']-pair['reference_seconds']/60)
            pairs.append(pair)
        results.append({**reference,'pairs':pairs,'table_status':'snap_failed' if snap is None else 'snapped',
            'local_snap_metres':None if snap is None else snap[2],'local_seconds':elapsed,'reached_cells':len(reached),
            'table_nearest10':nearest10({cid:value for cid,value in estimates.items() if value<=LIMIT_SECONDS})})
        print(f'Hybrid origins: {len(results)}/100; reached {len(reached)} cells; {elapsed:.6f} s',flush=True)
    times=[row['local_seconds'] for row in results];reached=[row['reached_cells'] for row in results]
    distances=[snap[2] for snap in snaps if snap is not None]
    summary={**accuracy_summary(results),'setup_seconds':setup_seconds,'hybrid_seconds':time.monotonic()-began,
        'local_runtime_mean_seconds':float(np.mean(times)),'local_runtime_p95_seconds':float(np.percentile(times,95)),
        'reached_cells_mean':float(np.mean(reached)),'local_budget_seconds':LOCAL_SECONDS,
        'local_snap_failed_origins':sum(row['local_snap_metres'] is None for row in results),
        'origins_without_reached_cells':sum(value==0 for value in reached),
        'cell_graph_snap_failed':sum(snap is None for snap in snaps),
        'cell_snap_metres_mean':float(np.mean(distances)) if distances else None,
        'cell_snap_metres_p95':float(np.percentile(distances,95)) if distances else None,
        'graph':'Exported modo-browser-csr-v1 full arrays, directed static segment seconds; no OSRM local/all-course queries',
        'matching':'Origins/centres project to nearest segment within 1000 m in state CRS, ties by endpoint indices; partial directed-edge costs included. Perpendicular offsets are matching distances, excluded from time as in OSRM; no off-road connector modeled.',
        'runtime_definition':'Per origin: segment snap, bounded directed search, reached-cell lookup and minimum composition; excludes shared graph/index/cell-snap setup',
        'error_definition':'abs((local seconds + stored whole cell minutes*60)/60 - same exact OSRM reference/60); missing estimates fail thresholds, no extra rounding or truncation for error',
        'nearest10_definition':'Exact seconds then course ID vs composed seconds then course ID, estimated <=90 min; same exact >=10 within90 eligible origins as plain',
        'qualification':'Browser graph uses static segment speeds without OSRM turn costs/restrictions; snapping can differ. Hybrid is an experiment, not guaranteed to improve accuracy.'}
    write_json(OUT/'hybrid-origins.json',{'seed':references['seed'],'origins':results})
    write_json(OUT/'hybrid-accuracy.json',summary)
    print('Hybrid complete: '+json.dumps(summary),flush=True)


if __name__=='__main__':main()
