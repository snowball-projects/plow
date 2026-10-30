"""State-only OSRM tables and fixed-reference scoring for overnight variants."""
import json
import math
import subprocess
import sys
import time
import numpy as np
from common import ROOT, WORK, OUT, STATE, SPACING, SNAPPING, TABLE_OUT, LIMIT_SECONDS, write_json, retained_minutes, nearest10, gzip_file, table_rows
from geometry import transforms, cells, centre, cell_key
from osrm import Client, QueryError

SOURCE_CHUNK=256
DESTINATION_CHUNK=128


def chunks(values,size):
    for i in range(0,len(values),size):yield values[i:i+size]


def start_server():
    from osrm_build import IMAGE,PARENT
    began=time.monotonic()
    current=subprocess.run(['docker','inspect','-f','{{.State.Running}} {{index .Config.Labels "plow.state"}}','plow-osrm'],capture_output=True,text=True)
    if current.returncode==0 and current.stdout.strip()=='true '+STATE:return Client(),0
    subprocess.run(['docker','rm','-f','plow-osrm'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','run','--detach','--name','plow-osrm','--cgroup-parent',PARENT,
        '--label','plow.state='+STATE,'-p','127.0.0.1:5000:5000','-v',str(WORK)+':/data:ro',IMAGE,
        'osrm-routed','--algorithm','ch','--threads','4','--max-table-size','1000','/data/roads.osrm'],check=True)
    client=Client()
    for attempt in range(120):
        try:
            client.nearest((0,0))
            return client,time.monotonic()-began
        except Exception:
            if subprocess.check_output(['docker','inspect','-f','{{.State.Running}}','plow-osrm'],text=True).strip()!='true':
                raise RuntimeError(subprocess.check_output(['docker','logs','plow-osrm'],text=True,stderr=subprocess.STDOUT))
            time.sleep(1)
    raise RuntimeError('OSRM CH server did not become ready within 120 s')
def accuracy_summary(rows):
    eligible_pairs = [p for r in rows for p in r['pairs'] if p['reference_status'] == 'within_90']
    one = sum(p.get('absolute_error_minutes', math.inf) <= 1 for p in eligible_pairs)
    two = sum(p.get('absolute_error_minutes', math.inf) <= 2 for p in eligible_pairs)
    top_eligible = [r for r in rows if len(r['exact_nearest10']) == 10]
    ordered = sum(r['exact_nearest10'] == r['table_nearest10'] for r in top_eligible)
    return {'origins': len(rows), 'exact_within_90_pairs': len(eligible_pairs),
            'origin_snap_failed': sum(r.get('origin_snap', {}).get('status') == 'snap_failed' for r in rows),
            'origin_snap_succeeded': sum(r.get('origin_snap', {}).get('status') == 'snapped' for r in rows),
            'origins_with_exact_within90': sum(any(p['reference_status'] == 'within_90' for p in r['pairs']) for r in rows),
            'origins_without_exact_within90': sum(not any(p['reference_status'] == 'within_90' for p in r['pairs']) for r in rows),
            'candidate_origin_course_pairs': sum(len(r['pairs']) for r in rows),
            'successful_reference_pairs': sum(p['reference_status'] in {'within_90', 'over_90'} for r in rows for p in r['pairs']),
            'over90_reference_pairs': sum(p['reference_status'] == 'over_90' for r in rows for p in r['pairs']),
            'origin_snap_failed_pairs': sum(p['reference_status'] == 'origin_snap_failed' for r in rows for p in r['pairs']),
            'courses_with_exact_within90': len({p['course_id'] for p in eligible_pairs if 'course_id' in p}),
            'table_snap_failed_origins': sum(r.get('table_status') == 'snap_failed' for r in rows),
            'table_missing_cell_origins': sum(r.get('table_status') == 'cell_missing' for r in rows),
            'within_1_minute_count': one, 'within_2_minutes_count': two,
            'within_1_minute_share': one / len(eligible_pairs) if eligible_pairs else None,
            'within_2_minutes_share': two / len(eligible_pairs) if eligible_pairs else None,
            'nearest10_eligible_origins': len(top_eligible), 'nearest10_same_order_count': ordered,
            'nearest10_same_order_share': ordered / len(top_eligible) if top_eligible else None,
            'missing_table_for_exact_within90': sum(p.get('table_minutes') is None for p in eligible_pairs),
            'matrix_error_pairs': sum(p['reference_status'] == 'matrix_error' for r in rows for p in r['pairs']),
            'matrix_null_pairs': sum(p['reference_status'] == 'matrix_null' for r in rows for p in r['pairs']),
            'nearest10_definition': 'Exact point-to-course seconds then course_id vs encoded cell-centre minutes then course_id; top-10 ID sequences identical, exact >=10 within90 denominator; missing/short table fails',
            'error_definition': 'abs(encoded whole-minute centre time - exact point matrix seconds/60); exact <=5400s pairs denominator, missing centre entry fails both thresholds'}



def courses_for(client):
    path=OUT/'course-snaps.json'
    if path.exists():
        saved=json.loads(path.read_text())
        if not saved:raise RuntimeError('No snapped course destinations')
        return saved
    courses=json.loads((OUT/'courses.json').read_text());usable=[]
    for course in courses:
        target=course['destination']
        if target:
            point=(target['longitude'],target['latitude']);course['snap']=client.nearest(point)
            if course['snap']['status']=='snapped':usable.append({**course,'point':point,'hint':course['snap']['hint']})
    coverage={**json.loads((OUT/'catalog-counts.json').read_text()),'snapped_course_records':len(usable),
              'failed_course_snaps':sum(c.get('snap',{}).get('status')=='snap_failed' for c in courses)}
    write_json(OUT/'courses.json',courses);write_json(OUT/'course-coverage.json',coverage);write_json(path,usable)
    print('Course coverage: '+json.dumps(coverage),flush=True)
    if not usable:raise RuntimeError('No course coordinates snapped within 1000 m')
    return usable


def build_table():
    from shapely.geometry import shape
    from shapely.ops import transform
    to_xy,to_ll,crs=transforms(STATE)
    polygon=transform(to_xy,shape(json.loads((WORK/'state.geojson').read_text())['geometry']))
    client,server_seconds=start_server();began=time.monotonic();usable=courses_for(client)
    keys=cells(polygon,SPACING);graph_snaps=None;graph=None
    if SNAPPING=='drivable':
        from hybrid import Graph
        graph=Graph.load(OUT/'browser/full');graph_snaps=graph.snap([centre(k,SPACING) for k in keys],True)
    table={};points=[];snap_distances=[];engine_distances=[]
    for index,key in enumerate(keys):
        point=to_ll(*centre(key,SPACING))
        if graph_snaps is not None:
            snap=graph_snaps[index]
            point=to_ll(*graph.position(snap)) if snap is not None else None
            offset=None if snap is None else snap[2]
        else:offset=None
        match=client.nearest(point) if point is not None else {'status':'snap_failed'}
        if graph_snaps is None:offset=match.get('distance_metres')
        table[key]={'cell':key,'status':match['status'],'minutes':{},'matrix_null_course_ids':[],'matrix_error_course_ids':[]}
        if match['status']=='snapped':
            points.append({'key':key,'point':point,'hint':match['hint']})
            snap_distances.append(offset)
            engine_distances.append(match['distance_metres'])
        if (index+1)%10000==0:print(f'Cell snaps {STATE} {SPACING} {SNAPPING}: {index+1}/{len(keys)}',flush=True)
    counts={'cells':len(table),'cell_snap_failed':len(table)-len(points),'matrix_calls':0,'matrix_error_pairs':0,
            'matrix_null_pairs':0,'requested_pairs':0,'pairs_within90':0,'cell_metres':SPACING,'snapping':SNAPPING,
            'crs':crs,'source_chunk':SOURCE_CHUNK,'destination_chunk':DESTINATION_CHUNK,'snap_seconds':time.monotonic()-began,
            'scope':'Centres strictly inside state polygon; zero road buffer; explicit nulls/errors, no fallback_speed'}
    counts['matching_policy']='Eligible modo segment pre-snap, then stock OSRM resnap; coincident road class not guaranteed' if SNAPPING=='drivable' else 'Original cell point with stock OSRM nearest matching'
    matrix_start=time.monotonic()
    for batch in chunks(points,SOURCE_CHUNK):
        for targets in chunks(usable,DESTINATION_CHUNK):
            counts['matrix_calls']+=1;counts['requested_pairs']+=len(batch)*len(targets)
            try:rows=client.table(batch,targets)
            except QueryError as error:
                counts['matrix_error_pairs']+=len(batch)*len(targets)
                for point in batch:table[point['key']]['matrix_error_course_ids'] += [c['course_id'] for c in targets]
                print('Table query error: '+str(error),flush=True);continue
            for point,row in zip(batch,rows):
                record=table[point['key']]
                for course,seconds in zip(targets,row):
                    cid=course['course_id']
                    if seconds is None:
                        counts['matrix_null_pairs']+=1;record['matrix_null_course_ids'].append(cid)
                    elif retained_minutes(seconds) is not None:
                        record['minutes'][cid]=retained_minutes(seconds);counts['pairs_within90']+=1
        if counts['matrix_calls']%20==0:print('Table progress: '+json.dumps(counts),flush=True)
    counts['matrix_seconds']=time.monotonic()-matrix_start;encode_start=time.monotonic()
    directory=TABLE_OUT/'tables';directory.mkdir(parents=True,exist_ok=True);handles={}
    try:
        for key,record in table.items():
            area=f'{key[0]//(40000//SPACING)}_{key[1]//(40000//SPACING)}'
            if area not in handles:handles[area]=(directory/(area+'.jsonl')).open('w')
            handles[area].write(json.dumps(record,separators=(',',':'))+'\n')
    finally:
        for handle in handles.values():handle.close()
    sizes={p.name:gzip_file(p) for p in sorted(directory.glob('*.jsonl'))}
    write_json(TABLE_OUT/'table-sizes.json',{'areas':sizes,'total_raw_bytes':sum(v['raw_bytes'] for v in sizes.values()),'total_gzip_bytes':sum(v['gzip_bytes'] for v in sizes.values())})
    counts.update({'encode_seconds':time.monotonic()-encode_start,'table_seconds':time.monotonic()-began,
        'server_start_seconds':server_seconds,'payload_complete':counts['matrix_error_pairs']==0,
        'cell_snap_metres_mean':sum(snap_distances)/len(snap_distances) if snap_distances else None,
        'cell_snap_metres_p95':float(np.percentile(snap_distances,95)) if snap_distances else None,
        'osrm_snap_metres_mean':float(np.mean(engine_distances)) if engine_distances else None,
        'osrm_snap_metres_p95':float(np.percentile(engine_distances,95)) if engine_distances else None})
    write_json(TABLE_OUT/'table-manifest.json',counts)
    print('Table complete: '+json.dumps(counts),flush=True)
    print('Table sizes: '+(TABLE_OUT/'table-sizes.json').read_text(),flush=True)


def create_reference():
    client,start_seconds=start_server();usable=courses_for(client)
    samples=json.loads((OUT/'sample-origins.json').read_text());results=[];began=time.monotonic()
    for origin in samples['origins']:
        point=(origin['longitude'],origin['latitude']);snap=client.nearest(point);exact={};pairs=[]
        for targets in chunks(usable,DESTINATION_CHUNK):
            try:durations=client.table([{'point':point,'hint':snap.get('hint','')}],targets)[0];failure=None
            except QueryError as error:durations=[None]*len(targets);failure=error.code
            for course,duration in zip(targets,durations):
                status=('within_90' if duration<=LIMIT_SECONDS else 'over_90') if duration is not None else ('origin_snap_failed' if snap['status']=='snap_failed' else 'matrix_error' if failure else 'matrix_null')
                pairs.append({'course_id':course['course_id'],'reference_status':status,'reference_seconds':duration,'table_minutes':None,'error_code':failure})
                if status=='within_90':exact[course['course_id']]=duration
        results.append({**origin,'origin_snap':snap,'pairs':pairs,'exact_nearest10':nearest10(exact),'table_nearest10':[]})
        print(f'Fixed reference origins: {len(results)}/100',flush=True)
    write_json(OUT/'reference-origins.json',{**samples,'origins':results})
    offsets=[r['origin_snap']['distance_metres'] for r in results if r['origin_snap']['status']=='snapped']
    write_json(OUT/'reference-summary.json',{**accuracy_summary(results),'reference_seconds':time.monotonic()-began,'server_start_seconds':start_seconds,
        'origin_snap_metres_mean':float(np.mean(offsets)) if offsets else None,
        'origin_snap_metres_p95':float(np.percentile(offsets,95)) if offsets else None,
        'qualification':'New fixed interior100, not prior unrestricted sample; clipped-state OSRM truth only, cross-border optimal routes can be absent even 50 km inside'})
    print('Fixed reference complete: '+(OUT/'reference-summary.json').read_text(),flush=True)


def score_plain():
    references=json.loads((OUT/'reference-origins.json').read_text());to_xy,_,_=transforms(STATE)
    wanted={cell_key(*to_xy(o['longitude'],o['latitude']),SPACING) for o in references['origins']};cache={}
    for record in table_rows(TABLE_OUT/'tables'):
        key=tuple(record['cell'])
        if key in wanted:cache[key]=record
    results=[]
    for origin in references['origins']:
        key=cell_key(*to_xy(origin['longitude'],origin['latitude']),SPACING)
        approximate=cache.get(key,{'status':'cell_missing','minutes':{}});pairs=[]
        for original in origin['pairs']:
            pair={**original,'table_minutes':approximate['minutes'].get(original['course_id'])}
            if pair['reference_status']=='within_90' and pair['table_minutes'] is not None:
                pair['absolute_error_minutes']=abs(pair['table_minutes']-pair['reference_seconds']/60)
            pairs.append(pair)
        results.append({**origin,'pairs':pairs,'cell':key,'table_status':approximate['status'],
                        'table_nearest10':nearest10(approximate['minutes'])})
    summary={**accuracy_summary(results),'cell_metres':SPACING,'snapping':SNAPPING,'reference':'unchanged original real-point OSRM references for all 100 interior origins'}
    write_json(TABLE_OUT/'accuracy-origins.json',{'seed':references['seed'],'origins':results})
    write_json(TABLE_OUT/'accuracy.json',summary);print('Plain accuracy: '+json.dumps(summary),flush=True)


if __name__=='__main__':
    {'table':build_table,'reference':create_reference,'score':score_plain}[sys.argv[1]]()
