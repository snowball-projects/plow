"""In-state grid sources to course destinations using OpenTimes-style CH chunks."""
import json
import math
import subprocess
import time
from common import ROOT, WORK, OUT, STATE, LIMIT_SECONDS, write_json, retained_minutes, nearest10, gzip_file
from geometry import transforms, cells, centre, cell_key
from osrm import Client, QueryError

SOURCE_CHUNK=256
DESTINATION_CHUNK=128


def chunks(values,size):
    for i in range(0,len(values),size):yield values[i:i+size]


def start_server():
    from osrm_build import IMAGE,PARENT
    began=time.monotonic()
    subprocess.run(['docker','run','--detach','--name','plow-osrm','--cgroup-parent',PARENT,
        '-p','127.0.0.1:5000:5000','-v',str(WORK)+':/data:ro',IMAGE,
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



def route_pilot():
    from shapely.geometry import shape
    from shapely.ops import transform
    to_xy,to_ll,crs=transforms(STATE)
    polygon=transform(to_xy,shape(json.loads((WORK/'state.geojson').read_text())))
    client,start_seconds=start_server();began=time.monotonic()
    courses=json.loads((OUT/'courses.json').read_text());usable=[]
    for course in courses:
        d=course['destination']
        if d:
            point=(d['longitude'],d['latitude']);course['snap']=client.nearest(point)
            if course['snap']['status']=='snapped':usable.append({**course,'point':point,'hint':course['snap']['hint']})
    coverage={**json.loads((OUT/'catalog-counts.json').read_text()),'snapped_course_records':len(usable),
              'failed_course_snaps':sum(c.get('snap',{}).get('status')=='snap_failed' for c in courses)}
    write_json(OUT/'courses.json',courses);write_json(OUT/'course-coverage.json',coverage)
    print('Course coverage: '+json.dumps(coverage),flush=True)
    if not usable:raise RuntimeError('No course coordinates snapped within 1000 m')
    table={};points=[]
    for key in cells(polygon):
        point=to_ll(*centre(key));snap=client.nearest(point)
        table[key]={'cell':key,'status':snap['status'],'minutes':{},'matrix_null_course_ids':[],'matrix_error_course_ids':[]}
        if snap['status']=='snapped':points.append({'key':key,'point':point,'hint':snap['hint']})
    counts={'cells':len(table),'cell_snap_failed':len(table)-len(points),'matrix_calls':0,'matrix_error_pairs':0,
            'matrix_null_pairs':0,'requested_pairs':0,'pairs_within90':0,'source_chunk':SOURCE_CHUNK,'destination_chunk':DESTINATION_CHUNK,
            'crs':crs,'cell_metres':2000,'scope':'cell centres inside state; routes may leave state within buffered graph',
            'rounding':'raw <=5400 s first; floor(seconds/60+0.5)','snap_policy':'1000 m nearest matching; no fallback_speed'}
    for batch in chunks(points,SOURCE_CHUNK):
        for destinations in chunks(usable,DESTINATION_CHUNK):
            counts['matrix_calls']+=1;counts['requested_pairs']+=len(batch)*len(destinations)
            try:rows=client.table(batch,destinations)
            except QueryError as error:
                counts['matrix_error_pairs']+=len(batch)*len(destinations)
                for point in batch:table[point['key']]['matrix_error_course_ids'] += [c['course_id'] for c in destinations]
                print('Table query error: '+str(error),flush=True)
                continue
            for point,row in zip(batch,rows):
                record=table[point['key']]
                for course,seconds in zip(destinations,row):
                    cid=course['course_id']
                    if seconds is None:
                        counts['matrix_null_pairs']+=1;record['matrix_null_course_ids'].append(cid)
                    elif retained_minutes(seconds) is not None:
                        record['minutes'][cid]=retained_minutes(seconds);counts['pairs_within90']+=1
        print('Table progress: '+json.dumps(counts),flush=True)
    directory=OUT/'tables';directory.mkdir();handles={}
    try:
        for key,record in table.items():
            area=f'{key[0]//20}_{key[1]//20}'
            if area not in handles:handles[area]=(directory/(area+'.jsonl')).open('w')
            handles[area].write(json.dumps(record,separators=(',',':'))+'\n')
    finally:
        for handle in handles.values():handle.close()
    sizes={p.name:gzip_file(p) for p in sorted(directory.glob('*.jsonl'))}
    write_json(OUT/'table-sizes.json',{'areas':sizes,'total_raw_bytes':sum(v['raw_bytes'] for v in sizes.values()),'total_gzip_bytes':sum(v['gzip_bytes'] for v in sizes.values())})
    counts['table_seconds']=time.monotonic()-began;counts['server_start_seconds']=start_seconds
    write_json(OUT/'table-manifest.json',counts);print('Table complete: '+json.dumps(counts),flush=True)
    samples=json.loads((OUT/'sample-origins.json').read_text());wanted={cell_key(*to_xy(o['longitude'],o['latitude'])) for o in samples['origins']};cache={}
    # Read emitted rows rather than reconstructing centre times during accuracy.
    for path in directory.glob('*.jsonl'):
        for line in path.open():
            record=json.loads(line);key=tuple(record['cell'])
            if key in wanted:cache[key]=record
    began=time.monotonic();results=[];calls=0
    for origin in samples['origins']:
        point=(origin['longitude'],origin['latitude']);snap=client.nearest(point);key=cell_key(*to_xy(*point))
        approximate=cache.get(key,{'status':'cell_missing','minutes':{}});exact={};pairs=[]
        for targets in chunks(usable,DESTINATION_CHUNK):
            calls+=1
            try:seconds=client.table([{'point':point,'hint':snap.get('hint','')}],targets)[0];failure=None
            except QueryError as error:seconds=[None]*len(targets);failure=error.code
            for course,duration in zip(targets,seconds):
                cid=course['course_id'];status=('within_90' if duration<=LIMIT_SECONDS else 'over_90') if duration is not None else ('origin_snap_failed' if snap['status']=='snap_failed' else 'matrix_error' if failure else 'matrix_null')
                pair={'course_id':cid,'reference_status':status,'reference_seconds':duration,'table_minutes':approximate['minutes'].get(cid),'error_code':failure}
                if status=='within_90':
                    exact[cid]=duration
                    if pair['table_minutes'] is not None:pair['absolute_error_minutes']=abs(pair['table_minutes']-duration/60)
                pairs.append(pair)
        results.append({**origin,'origin_snap':snap,'cell':key,'table_status':approximate['status'],'pairs':pairs,'exact_nearest10':nearest10(exact),'table_nearest10':nearest10(approximate['minutes'])})
        print(f'Accuracy origins: {len(results)}/100',flush=True)
    accuracy={**accuracy_summary(results),'matrix_calls':calls,'accuracy_seconds':time.monotonic()-began,
              'definition':'OSRM CH point-to-course vs emitted cell-centre whole minutes; discretization/encoding accuracy, not real-world validation'}
    write_json(OUT/'accuracy-origins.json',{'seed':samples['seed'],'distribution':samples['distribution'],'origins':results})
    write_json(OUT/'accuracy.json',accuracy);print('Accuracy complete: '+json.dumps(accuracy),flush=True)
    print('Table sizes: '+(OUT/'table-sizes.json').read_text(),flush=True)


if __name__=='__main__':
    try:route_pilot()
    finally:subprocess.run(['docker','rm','-f','plow-osrm'],check=False)
