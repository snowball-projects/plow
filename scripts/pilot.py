"""Bounded free-runner supervisor. All measured results go to Actions output."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from common import ROOT, WORK, OUT, STATE, write_json

GIB = 1024**3
DISK_RESERVE = 10*GIB
TIME_LIMIT = 5*3600


class CapacityStop(Exception):
    pass


def disk_bytes():
    # st_blocks measures actual working storage, including sparse build files.
    size=0
    for path in ROOT.rglob('*'):
        try:
            if path.is_file():size+=path.stat().st_blocks*512
        except FileNotFoundError:
            continue  # Build processes may delete temporary files while sampled.
    return size


def workspace_space():
    value = os.statvfs(ROOT)
    device = os.stat(ROOT).st_dev
    return {'available_bytes': value.f_bavail * value.f_frsize,
            'free_bytes': value.f_bfree * value.f_frsize,
            'total_bytes': value.f_blocks * value.f_frsize,
            'available_inodes': value.f_favail,
            'device_id': f'{os.major(device)}:{os.minor(device)}'}


def filesystem_evidence():
    return {'workspace': str(ROOT), 'space': workspace_space(),
            'df_B1': subprocess.check_output(['df', '-B1', str(ROOT)], text=True),
            'findmnt': json.loads(subprocess.check_output(
                ['findmnt', '--json', '--target', str(ROOT),
                 '--output', 'SOURCE,TARGET,FSTYPE,MAJ:MIN'], text=True))}


def cgroup_path():
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        if line.startswith('0::'):
            return (Path('/sys/fs/cgroup') / line.split('::',1)[1].lstrip('/')).parent
    raise RuntimeError('cgroup v2 required for measured aggregate memory')


def read_number(path):
    try: return int(path.read_text().strip())
    except (FileNotFoundError,ValueError): return None


def memory_stat(cg):
    return {key: int(value) for key, value in
            (line.split() for line in (cg/'memory.stat').read_text().splitlines())
            if key in {'anon','file','kernel','shmem','inactive_file','active_file'}}


def verify_memory_bounds(cg):
    maximum=read_number(cg/'memory.max');swap=read_number(cg/'memory.swap.max')
    if maximum != 13*GIB or swap != 0:
        raise RuntimeError('Pilot requires enforced cgroup memory.max=13 GiB and memory.swap.max=0')
    return {'memory_max_bytes':maximum,'memory_swap_max_bytes':swap}


def national_job_seconds(stages, table_seconds, accuracy_seconds, state_ratios):
    build=sum(stages.get(name,0) for name in ['prepare','clip','extract','contract'])
    return {state:build*ratios['volume']+table_seconds*ratios['pairs']+
            accuracy_seconds*ratios['courses']+stages.get('browser_graph',0)*ratios['raw']+
            stages.get('image',0) for state,ratios in state_ratios.items()}


def metric_table(report):
    def load(name):
        path=OUT/(name+'.json')
        return json.loads(path.read_text()) if path.exists() else None
    source=load('sources');grid=load('table-manifest');size=load('table-sizes');acc=load('accuracy');browser=load('browser-sizes');coverage=load('course-coverage');network=load('network')
    missing='unavailable: '+report.get('stop_reason',report.get('error','stage not reached'))
    rows=[['Metric','Measured state pilot','Nationwide planning scenario'],
        ['State / buffered inputs',f"{STATE}; {source['input_bytes']} bytes; {len(source['extracts'])} extracts; polygon coverage {source['source_union_buffer_area_share']:.3%}" if source else missing,'300 km buffered state jobs; overlapping inputs are repeated per job'],
        *[[f"Stage {stage['name']}",f"{stage['elapsed_seconds']:.2f} s ({stage['status']})",''] for stage in report['stages']]]
    for name in ['merge','buffer_clip','browser_clip','road_filter','osrm_extract','osrm_contract']:
        value=load(name)
        if value:rows.append([name,f"{value['seconds']:.2f} s",'included in stages above'])
    rows += [
        ['CH network',f"{network['bytes']} bytes" if network else missing,'no national network built'],
        ['Table / accuracy / server start',f"{grid['table_seconds']:.2f} / {acc['accuracy_seconds']:.2f} / {grid['server_start_seconds']:.2f} s" if grid and acc else missing,'table includes cell/course snap checks and encoding'],
        ['Peak aggregate cgroup memory',f"{report['peak_cgroup_memory_bytes']/GIB:.3f} GiB incl. page cache; sampled anon {report['peak_observed_anon_bytes']/GIB:.3f} GiB",'RAM is a bounded working set; largest states are unproven'],
        ['Disk increase / peak workspace / minimum free',f"{(report['initial_workspace_space']['available_bytes']-report['minimum_free_disk_bytes'])/GIB:.3f} / {report['peak_working_disk_bytes']/GIB:.3f} / {report['minimum_free_disk_bytes']/GIB:.3f} GiB",'increase includes Docker image; workspace excludes Docker image store'],
        ['Course coverage',f"{coverage['course_records']} records; {coverage['with_published_coordinates']} coordinates; {coverage['missing_coordinates']} missing; {coverage['distinct_destinations']} destination IDs; {coverage['snapped_course_records']} snapped; {coverage['failed_course_snaps']} failed snaps" if coverage else missing,'all course records retained, no access filter'],
        ['Grid / requested / retained / null / error pairs',f"{grid['cells']} centres ({grid['cell_snap_failed']} failed snaps); {grid['requested_pairs']} / {grid['pairs_within90']} / {grid['matrix_null_pairs']} / {grid['matrix_error_pairs']}; {grid['matrix_calls']} table calls" if grid else missing,'null and query errors never replaced by straight-line times'],
        ['Tables raw / gzip',f"{size['total_raw_bytes']} / {size['total_gzip_bytes']} bytes" if size else missing,'per-area details printed in logs and output/table-sizes.json'],
        ['Per-area raw / gzip ranges',f"{len(size['areas'])} areas; raw {min(v['raw_bytes'] for v in size['areas'].values())}..{max(v['raw_bytes'] for v in size['areas'].values())}; gzip {min(v['gzip_bytes'] for v in size['areas'].values())}..{max(v['gzip_bytes'] for v in size['areas'].values())}" if size and size['areas'] else missing,'40 km square partitions'],
        ['Accuracy coverage',f"{acc['origin_snap_succeeded']}/{acc['origins']} origins snapped; {acc['origin_snap_failed']} failed; {acc['exact_within_90_pairs']} eligible pairs; {acc['missing_table_for_exact_within90']} missing centre pairs; {acc['matrix_error_pairs']} errors; {acc['matrix_null_pairs']} nulls" if acc else missing,'100 uniform-area random origins; seed 20261029'],
        ['Within 1 / 2 minutes',f"{acc['within_1_minute_count']}/{acc['exact_within_90_pairs']} ({acc['within_1_minute_share']}); {acc['within_2_minutes_count']}/{acc['exact_within_90_pairs']} ({acc['within_2_minutes_share']})" if acc else missing,'missing centre times fail both thresholds'],
        ['Nearest-10 same ordered IDs',f"{acc['nearest10_same_order_count']}/{acc['nearest10_eligible_origins']} ({acc['nearest10_same_order_share']}); {100-acc['nearest10_eligible_origins']} ineligible" if acc else missing,'exact seconds then ID vs whole-minute centre time then ID; exact >=10 courses within 90 min'],
        ['Browser full / tiled gzip',f"{browser['full']['gzip_bytes']} / {browser['tiles']['gzip_bytes']} bytes" if browser else missing,'modo format; segment speed profile does not encode OSRM turn costs']]
    estimate=load('estimate-inputs')
    if estimate and report['status']=='complete' and grid and acc and browser:
        stages={r['name']:r['elapsed_seconds'] for r in report['stages']}
        jobs=national_job_seconds(stages,grid['table_seconds'],acc['accuracy_seconds'],estimate['state_ratios'])
        total=sum(jobs.values());largest=max(jobs.values())
        rows += [['50 state jobs, total runner time',f"{total/3600:.2f} runner-hours (hypothesis)",'build: area-weighted buffered compressed-input proxy; table: cell-area x public-coordinate course pairs; browser: state input bytes'],
            ['Parallel makespan',f"one slot {total/3600:.2f} h; ideal four-slot lower bound {max(total/4,largest)/3600:.2f} h",'template cap 4; available account slots unmeasured, queues/scheduling add delay; no guarantee largest states fit'],
            ['National table gzip scenario',f"{size['total_gzip_bytes']*estimate['pair_ratio']:.0f} bytes",'cell-course-pair size proxy assumes Illinois retained/null fractions and encoding density; high uncertainty'],
            ['National browser gzip scenario',f"{browser['tiles']['gzip_bytes']*estimate['raw_input_ratio']:.0f} bytes",'whole-OSM state input-volume proxy, not measured road arcs']]
    else:rows.append(['Nationwide estimate',missing,'requires completed baselines; no nationwide execution'])
    return '| '+' | '.join(rows[0])+' |\n|---|---|---|\n'+''.join('| '+' | '.join(str(v).replace('|','/') for v in row)+' |\n' for row in rows[1:])


def run_stage(name, report, cg, started, command=None):
    stage = {'name':name, 'status':'running', 'peak_working_disk_bytes':0,
             'minimum_free_disk_bytes':workspace_space()['available_bytes']}
    report['stages'].append(stage)
    begin = time.monotonic(); last_print = begin - 60
    time_path = OUT / (name + '-time.txt')
    # Inherit stdout: batch counters and completed stage metrics survive Actions
    # cancellation instead of being hidden in a temporary runner-only log.
    proc = subprocess.Popen(['/usr/bin/time','-v','-o',str(time_path), sys.executable,
        '-u',*[str(ROOT/'scripts'/part) if i==0 else part for i,part in enumerate(command or [name+'.py'])]], cwd=WORK, start_new_session=True)
    try:
        while True:
            memory = read_number(cg/'memory.current')
            peak = read_number(cg/'memory.peak')
            composition = memory_stat(cg); disk = disk_bytes(); space = workspace_space()
            free = space['available_bytes']; now = time.monotonic()
            report['peak_cgroup_memory_bytes'] = max(report['peak_cgroup_memory_bytes'], peak or memory or 0)
            report['peak_working_disk_bytes'] = max(report['peak_working_disk_bytes'], disk)
            report['minimum_free_disk_bytes'] = min(report['minimum_free_disk_bytes'], free)
            report['last_memory_stat'] = composition
            report['peak_observed_anon_bytes'] = max(report['peak_observed_anon_bytes'], composition.get('anon',0))
            report['last_workspace_space'] = space
            report['peak_filesystem_used_bytes'] = max(report['peak_filesystem_used_bytes'], space['total_bytes']-space['free_bytes'])
            stage.update({'elapsed_seconds':now-begin, 'last_memory_bytes':memory,
                'last_disk_bytes':disk, 'last_free_disk_bytes':free,
                'peak_working_disk_bytes':max(stage['peak_working_disk_bytes'],disk),
                'minimum_free_disk_bytes':min(stage['minimum_free_disk_bytes'],free)})
            report['elapsed_seconds'] = now-started
            write_json(OUT/'report.json', report)
            if now-last_print >= 60:
                print('Resource checkpoint: ' + json.dumps(report), flush=True); last_print = now
            if free < DISK_RESERVE:
                raise CapacityStop(f'{name}: workspace filesystem available disk {free} bytes below {DISK_RESERVE} byte system/packaging reserve')
            if now-started >= TIME_LIMIT:
                raise CapacityStop(f'{name}: five-hour processing budget exhausted')
            code = proc.poll()
            if code is not None:
                stage['returncode'] = code
                if code and 'oom_kill 0' not in (cg/'memory.events').read_text():
                    raise CapacityStop(f'{name}: actual OOM at enforced 13 GiB memory limit')
                if code: raise RuntimeError(f'{name} failed with exit {code}; see Actions log')
                stage['status'] = 'complete'; break
            time.sleep(2)
    except BaseException:
        if proc.poll() is None:
            os.killpg(proc.pid,signal.SIGTERM)
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL); proc.wait()
        stage['status'] = 'stopped'
        raise
    finally:
        stage['elapsed_seconds'] = time.monotonic()-begin
        print('Stage outcome: ' + json.dumps(stage), flush=True)
        if time_path.exists(): print(time_path.read_text(), flush=True)


def main():
    if os.environ.get('GITHUB_SHA') is None:
        raise SystemExit('Road work runs only through the manual Actions workflow')
    WORK.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
    cg=cgroup_path();started=time.monotonic()
    enforced_memory=verify_memory_bounds(cg)
    filesystem=filesystem_evidence()
    write_json(OUT/'filesystem-initial.json',filesystem)
    print('Initial workspace filesystem: '+json.dumps(filesystem),flush=True)
    report={'status':'running','state':STATE,'implementation_sha':os.environ['GITHUB_SHA'],
        'stages':[],'peak_cgroup_memory_bytes':0,'peak_observed_anon_bytes':0,'peak_working_disk_bytes':0,
        'enforced_memory':enforced_memory,
        'initial_memory_stat':memory_stat(cg),
        'initial_workspace_space':filesystem['space'],
        'last_workspace_space':filesystem['space'],
        'minimum_free_disk_bytes':filesystem['space']['available_bytes'],
        'peak_filesystem_used_bytes':filesystem['space']['total_bytes']-filesystem['space']['free_bytes'],
        'filesystem_evidence':filesystem,
        'limits':{'cpu':4,'osrm_threads':4,'memory_hard_bytes':13*GIB,'workspace_free_disk_reserve_bytes':DISK_RESERVE,'processing_seconds':TIME_LIMIT},
        'measurement':'Shared slice memory.peak includes supervisor and OSRM containers plus page cache, excludes Docker daemon/base OS; work storage uses allocated st_blocks incl. venv, inputs and intermediates; disk sampled every 2s; GNU time -v per stage also retained',
        'cost':'public standard ubuntu-24.04 only; no cache, release, data commit, paid runner, or laptop processing'}
    code=0
    try:
        for name,command in [('prepare',['prepare.py']),('clip',['osrm_build.py','clip']),('image',['osrm_build.py','image']),('extract',['osrm_build.py','extract']),('contract',['osrm_build.py','contract']),('routing',['routing.py']),('browser_graph',['browser_graph.py'])]:
            run_stage(name,report,cg,started,command)
        report['status']='complete'
    except CapacityStop as e:
        report['status']='capacity_stop';report['stop_reason']=str(e)
        # Capacity stop is a truthful completed preflight outcome, not success of
        # missing downstream stages. It remains distinct from tooling errors.
    except BaseException as e:
        report['status']='error';report['error']=f'{type(e).__name__}: {e}';code=1
    finally:
        for container in ['plow-osrm','plow-extract','plow-contract']:
            subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        report['elapsed_seconds']=time.monotonic()-started
        report['cgroup_events']=(cg/'memory.events').read_text()
        final_filesystem=filesystem_evidence()
        write_json(OUT/'filesystem-final.json',final_filesystem)
        print('Final workspace filesystem: '+json.dumps(final_filesystem),flush=True)
        write_json(OUT/'report.json',report)
        (OUT/'report.md').write_text(f"Pilot status: **{report['status']}**\n\n"+metric_table(report)+'\n\n'+
            'Measurements cover this runner only. Incomplete stages have no extrapolation.\n'
            'Raw inputs, merged PBFs and OSRM CH files are intermediates, excluded from output artifacts.\n'
            'Course property-point and missing-arrival qualifications are preserved in courses.json.\n')
        # Mark partial generated payloads so interrupted files can never be
        # mistaken for a complete table or browser bundle.
        write_json(OUT/'completion.json',{'status':report['status'],'complete':report['status']=='complete',
            'implementation_sha':report['implementation_sha'],'reason':report.get('stop_reason',report.get('error'))})
        print((OUT/'report.md').read_text(),flush=True)
    return code


if __name__=='__main__':
    sys.exit(main())
