"""Bounded free-runner supervisor. All measured results go to Actions output."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from common import ROOT, WORK, OUT, write_json

GIB = 1024**3
DISK_RESERVE = 10*GIB
TIME_LIMIT = 5*3600


class CapacityStop(Exception):
    pass


def disk_bytes():
    # st_blocks measures actual working storage, including sparse build files.
    return sum(p.stat().st_blocks*512 for p in ROOT.rglob('*') if p.is_file())


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
            return Path('/sys/fs/cgroup') / line.split('::',1)[1].lstrip('/')
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


def metric_table(report):
    stages={r['name']:r for r in report['stages']}
    reason=report.get('stop_reason') or report.get('error') or 'not reached'
    def load(name):
        path=OUT/name
        return json.loads(path.read_text()) if path.exists() else None
    tile=load('tile-build.json');sizes=load('table-sizes.json');acc=load('accuracy.json')
    browser=load('browser-sizes.json');counts=load('catalog-counts.json')
    coverage=load('course-coverage.json');merged=load('regional-input.json');grid=load('table-manifest.json')
    national=json.loads((ROOT/'pins/sources.json').read_text()).get('national_input_volume')
    missing='unavailable: '+reason
    build=stages.get('build_tiles')
    hypothetical_time='unsupported: completed buffered regional tile baseline unavailable'
    hypothetical_disk=hypothetical_time;hypothetical_tiles=hypothetical_time
    if tile and merged and national and build and build['status']=='complete':
        factor=national['bytes']/merged['clipped_pbf_bytes']
        qualifier=f'input-volume-only hypothesis x{factor:.3f}; compressed OSM includes non-roads, temporary working set is nonlinear'
        hypothetical_time=f"{build['elapsed_seconds']*factor/3600:.2f} h; "+qualifier
        hypothetical_disk=f"{build['peak_working_disk_bytes']*factor/GIB:.2f} GiB; "+qualifier
        hypothetical_tiles=f"{tile['tile_bytes']*factor:.0f} bytes; "+qualifier
    r=acc['random100'] if acc else None
    course_text=(f"{coverage['course_records']} records; {coverage['with_published_coordinates']} coordinates; "
        f"{coverage['missing_coordinates']} missing; {coverage['distinct_published_destination_ids']} distinct destinations; "
        f"{coverage['snapped_course_records']} snapped records; {coverage['failed_course_snap_records']} failed snaps") if coverage else (
        f"{counts['illinois_courses']} records; {counts['with_published_coordinates']} coordinates; {counts['missing_destination']} missing; "
        f"{counts['distinct_published_destination_ids']} distinct destinations; snaps not measured") if counts else missing
    table=[['Metric','Measured Illinois / regional pilot','Nationwide extrapolation'],
        ['Pinned regional extracts',f"{sum(x['bytes'] for x in json.loads((ROOT/'pins/extracts.json').read_text())):,} bytes, 6 states before clip",'200 km buffer intersected with six source extracts; see coverage row'],
        ['Road clip coverage',f"{merged['clipped_pbf_bytes']} PBF bytes; {merged['source_union_buffer_area_share']:.2%} of requested buffer covered by source polygons; Michigan excluded" if merged else missing,'no claim of complete 200 km coverage'],
        ['Illinois course coverage',course_text,'course records are distinct from regional road volume'],
        ['Supervised processing, excludes setup/apt/pip',f"{report.get('elapsed_seconds',0):.1f} s",'not total Actions wall time'],
        *[[f"Stage: {stage['name']}", f"{stage['elapsed_seconds']:.1f} s ({stage['status']})", 'measured, excludes apt/pip setup'] for stage in report['stages']],
        ['Regional merge + clip + tile phase',f"{build['elapsed_seconds']:.1f} s ({build['status']})" if build else missing,hypothetical_time],
        ['Peak aggregate cgroup memory',f"{report.get('peak_cgroup_memory_bytes',0)/GIB:.3f} GiB incl. page cache",'unsupported: RAM is a bounded working set; national peak/build strategy unmeasured'],
        ['Peak observed working disk',f"{report.get('peak_working_disk_bytes',0)/GIB:.3f} GiB, 2 s sampling",hypothetical_disk],
        ['Valhalla executable / merge / clip',f"{tile['tile_build_seconds']:.1f} / {merged['merge_seconds']:.1f} / {merged['clip_seconds']:.1f} s; four build threads" if tile and merged else missing,'stage sub-times'],
        ['Tile files / bytes',f"{tile['tile_files']} / {tile['tile_bytes']}" if tile else missing,hypothetical_tiles],
        ['Tables raw / gzip',f"{sizes['total_raw_bytes']} / {sizes['total_gzip_bytes']} bytes" if sizes else missing,'unsupported: national course-by-candidate-cell/encoded-pair denominator unmeasured'],
        ['Per-area raw / gzip ranges',f"{len(sizes['areas'])} areas; raw {min(x['raw_bytes'] for x in sizes['areas'].values())}..{max(x['raw_bytes'] for x in sizes['areas'].values())}; gzip {min(x['gzip_bytes'] for x in sizes['areas'].values())}..{max(x['gzip_bytes'] for x in sizes['areas'].values())} bytes" if sizes and sizes['areas'] else missing,'no national area-size claim'],
        ['Grid candidate/requested/encoded pairs',f"{grid['candidate_pairs_considered']} / {grid['matrix_pairs_requested']} / {grid['pairs_within90']}; {grid['table_generation_seconds']:.1f} s" if grid else missing,'unsupported: no national pair count; pilot pairs are Illinois destinations against Illinois cell centres'],
        ['Table matrix batches / snap failures',f"{grid['matrix_calls_completed']}/{grid['matrix_calls_planned']} calls; {grid['cells_considered']} Illinois centres; {grid['cell_snap_failed']} failed snaps; {grid['matrix_error_pairs']} batch-error pairs; {grid['matrix_null_pairs']} null pairs" if grid else missing,'reverse expansion per course-target batch'],
        ['Accuracy matrix calls',f"{acc['matrix_calls']}/112; {acc['accuracy_seconds']:.1f} s; same-algorithm discretization/encoding check" if acc else missing,'not independent route-realism validation'],
        ['Random-origin snap and matrix coverage',f"{r['origin_snap_succeeded']}/{r['origins']} snapped; {r['origin_snap_failed']} failed; {r['origins_with_exact_within90']} origins with <=90 min routes; {r['matrix_error_pairs']} matrix errors; {r['matrix_null_pairs']} null pairs; {r['exact_within_90_pairs']} eligible pairs; {r['missing_table_for_exact_within90']} missing table pairs" if r else missing,'conditional accuracy below must be read with these omissions'],
        ['Accuracy within 1 / 2 min',f"{r['within_1_minute_count']}/{r['exact_within_90_pairs']} / {r['within_2_minutes_count']}/{r['exact_within_90_pairs']} pairs; shares {r['within_1_minute_share']} / {r['within_2_minutes_share']}" if r else missing,'no national accuracy claim'],
        ['Nearest-10 same order',f"{r['nearest10_same_order_count']}/{r['nearest10_eligible_origins']} eligible origins; {r['origins']-r['nearest10_eligible_origins']} ineligible (<10 exact matrix routes)" if r else missing,'no national accuracy claim'],
        ['Illinois browser full / tiles gzip',f"{browser['full']['gzip_bytes']} / {browser['tiles']['gzip_bytes']} bytes" if browser else missing,'unsupported: national segment/arc population unmeasured; course count is not a road proxy'],
        ['Workspace available disk, initial / minimum',f"{report['initial_workspace_space']['available_bytes']/GIB:.3f} / {report['minimum_free_disk_bytes']/GIB:.3f} GiB; 10 GiB reserve",'measured filesystem space, not advertised runner SSD figure']]
    text='| '+' | '.join(table[0])+' |\n|---|---|---|\n'
    text+=''.join('| '+' | '.join(str(v).replace('|','/') for v in row)+' |\n' for row in table[1:])
    return text


def run_stage(name, report, cg, started):
    stage = {'name':name, 'status':'running', 'peak_working_disk_bytes':0,
             'minimum_free_disk_bytes':workspace_space()['available_bytes']}
    report['stages'].append(stage)
    begin = time.monotonic(); last_print = begin - 60
    time_path = OUT / (name + '-time.txt')
    # Inherit stdout: batch counters and completed stage metrics survive Actions
    # cancellation instead of being hidden in a temporary runner-only log.
    proc = subprocess.Popen(['/usr/bin/time','-v','-o',str(time_path), sys.executable,
        '-u',str(ROOT/'scripts'/(name+'.py'))], cwd=WORK, start_new_session=True)
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
    report={'status':'running','implementation_sha':os.environ['GITHUB_SHA'],
        'stages':[],'peak_cgroup_memory_bytes':0,'peak_observed_anon_bytes':0,'peak_working_disk_bytes':0,
        'enforced_memory':enforced_memory,
        'initial_memory_stat':memory_stat(cg),
        'initial_workspace_space':filesystem['space'],
        'last_workspace_space':filesystem['space'],
        'minimum_free_disk_bytes':filesystem['space']['available_bytes'],
        'peak_filesystem_used_bytes':filesystem['space']['total_bytes']-filesystem['space']['free_bytes'],
        'filesystem_evidence':filesystem,
        'limits':{'cpu':4,'tile_threads':4,'memory_hard_bytes':13*GIB,'workspace_free_disk_reserve_bytes':DISK_RESERVE,'processing_seconds':TIME_LIMIT},
        'measurement':'cgroup v2 memory.peak includes all descendants and page cache; work storage uses allocated st_blocks incl. venv, inputs and intermediates; disk sampled every 2s; GNU time -v per stage also retained',
        'cost':'public standard ubuntu-24.04 only; no cache, release, data commit, paid runner, or laptop processing'}
    code=0
    try:
        for name in ['prepare','build_tiles','routing','browser_graph']:
            run_stage(name,report,cg,started)
        report['status']='complete'
    except CapacityStop as e:
        report['status']='capacity_stop';report['stop_reason']=str(e)
        # Capacity stop is a truthful completed preflight outcome, not success of
        # missing downstream stages. It remains distinct from tooling errors.
    except BaseException as e:
        report['status']='error';report['error']=f'{type(e).__name__}: {e}';code=1
    finally:
        report['elapsed_seconds']=time.monotonic()-started
        report['cgroup_events']=(cg/'memory.events').read_text()
        final_filesystem=filesystem_evidence()
        write_json(OUT/'filesystem-final.json',final_filesystem)
        print('Final workspace filesystem: '+json.dumps(final_filesystem),flush=True)
        write_json(OUT/'report.json',report)
        (OUT/'report.md').write_text(f"Pilot status: **{report['status']}**\n\n"+metric_table(report)+'\n\n'+
            'Measurements cover this runner only. Incomplete stages have no extrapolation.\n'
            'Raw inputs, merged PBFs and Valhalla tiles are intermediates, excluded from output artifacts.\n'
            'Course property-point and missing-arrival qualifications are preserved in courses.json.\n')
        # Mark partial generated payloads so interrupted files can never be
        # mistaken for a complete table or browser bundle.
        write_json(OUT/'completion.json',{'status':report['status'],'complete':report['status']=='complete',
            'implementation_sha':report['implementation_sha'],'reason':report.get('stop_reason',report.get('error'))})
        print((OUT/'report.md').read_text(),flush=True)
    return code


if __name__=='__main__':
    sys.exit(main())
