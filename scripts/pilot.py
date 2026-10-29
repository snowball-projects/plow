"""Bounded free-runner supervisor. All measured results go to Actions output."""
import json
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

from common import ROOT, WORK, OUT, write_json

GIB = 1024**3
DISK_LIMIT = 12*GIB
MEMORY_STOP = int(12.5*GIB)  # cgroup hard limit 13 GiB, no swap
TIME_LIMIT = 5*3600


class CapacityStop(Exception):
    pass


def disk_bytes():
    # st_blocks measures actual working storage, including sparse build files.
    return sum(p.stat().st_blocks*512 for p in ROOT.rglob('*') if p.is_file())


def cgroup_path():
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        if line.startswith('0::'):
            return Path('/sys/fs/cgroup') / line.split('::',1)[1].lstrip('/')
    raise RuntimeError('cgroup v2 required for measured aggregate memory')


def read_number(path):
    try: return int(path.read_text().strip())
    except (FileNotFoundError,ValueError): return None


def metric_table(report):
    stages={r['name']:r for r in report['stages']}
    reason=report.get('stop_reason') or report.get('error') or 'not reached'
    def load(name):
        path=OUT/name
        return json.loads(path.read_text()) if path.exists() else None
    tile=load('tile-build.json');sizes=load('table-sizes.json');acc=load('accuracy.json');browser=load('browser-sizes.json');counts=load('catalog-counts.json')
    missing='unavailable: '+reason
    build=stages.get('build_tiles')
    table=[['Metric','Measured Illinois / regional pilot','Nationwide extrapolation'],
           ['Pinned regional extracts',f"{sum(x['bytes'] for x in json.loads((ROOT/'pins/extracts.json').read_text())):,} bytes (7 states)",'not a national road-volume proxy'],
           ['Illinois catalog courses / destinations',f"{counts['illinois_courses']} / {counts['with_destination']} ({counts['missing_destination']} missing)" if counts else missing,'4985 published catalog courses; no national build'],
           ['Tile build elapsed',f"{build['elapsed_seconds']:.1f} s ({build['status']})" if build else missing,'unavailable unless Illinois build completes'],
           ['Peak aggregate cgroup memory',f"{report.get('peak_cgroup_memory_bytes',0)/GIB:.3f} GiB incl. page cache",'not extrapolated from an incomplete build'],
           ['Peak observed working disk',f"{report.get('peak_working_disk_bytes',0)/GIB:.3f} GiB (2 s sampling)",'not extrapolated from an incomplete build'],
           ['Tile files / bytes',f"{tile['tile_files']} / {tile['tile_bytes']}" if tile else missing,'unavailable without completed tile build'],
           ['Tables raw / gzip',f"{sizes['total_raw_bytes']} / {sizes['total_gzip_bytes']} bytes" if sizes else missing,'unavailable without completed tables'],
           ['Per-area table files',f"{len(sizes['areas'])} areas; all byte sizes in table-sizes.json" if sizes else missing,'unavailable without completed tables'],
           ['Accuracy within 1 / 2 min',f"{acc['random100']['within_1_minute_share']} / {acc['random100']['within_2_minutes_share']} ({acc['random100']['exact_within_90_pairs']} pairs)" if acc else missing,'no national accuracy claim'],
           ['Nearest-10 same order',f"{acc['random100']['nearest10_same_order_count']} / {acc['random100']['nearest10_eligible_origins']} eligible origins" if acc else missing,'no national accuracy claim'],
           ['Illinois browser full / tiles gzip',f"{browser['full']['gzip_bytes']} / {browser['tiles']['gzip_bytes']} bytes" if browser else missing,'unavailable without completed Illinois graph']]
    # A simple, explicitly catalog-count-scaled scenario; no claim that national
    # road density, boundary overhead, parallel memory, or accuracy scales so.
    if report['status']=='complete' and counts and tile and sizes and browser:
        factor=counts['nationwide_courses']/counts['illinois_courses']
        table[3][2]=f"{build['elapsed_seconds']*factor/3600:.2f} h, count-scaled scenario x{factor:.3f}"
        table[4][2]=f"{report['peak_cgroup_memory_bytes']*factor/GIB:.2f} GiB, single merged-graph linear scenario; not a measured bound"
        table[5][2]=f"{report['peak_working_disk_bytes']*factor/GIB:.2f} GiB, linear scenario; regional halo duplicated"
        table[6][2]=f"{tile['tile_bytes']*factor:.0f} bytes, same count-scaled scenario"
        table[7][2]=f"{sizes['total_raw_bytes']*factor:.0f} / {sizes['total_gzip_bytes']*factor:.0f} bytes, catalog-count scaling"
        table[11][2]=f"{browser['full']['gzip_bytes']*factor:.0f} / {browser['tiles']['gzip_bytes']*factor:.0f} bytes, weak catalog-count proxy for roads"
    text='| '+' | '.join(table[0])+' |\n|---|---|---|\n'
    text+=''.join('| '+' | '.join(str(v).replace('|','/') for v in row)+' |\n' for row in table[1:])
    return text


def run_stage(name, report, cg, started):
    log_path=WORK/(name+'.log')
    stage={'name':name,'status':'running'}
    report['stages'].append(stage)
    begin=time.monotonic()
    with log_path.open('w') as log:
        proc=subprocess.Popen(['/usr/bin/time','-v','-o',str(OUT/(name+'-time.txt')),
            sys.executable,str(ROOT/'scripts'/(name+'.py'))],cwd=WORK,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while True:
                memory=read_number(cg/'memory.current')
                peak=read_number(cg/'memory.peak')
                disk=disk_bytes()
                free=os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize
                report['peak_cgroup_memory_bytes']=max(report['peak_cgroup_memory_bytes'],peak or memory or 0)
                report['peak_working_disk_bytes']=max(report['peak_working_disk_bytes'],disk)
                report['minimum_free_disk_bytes']=min(report['minimum_free_disk_bytes'],free)
                stage.update({'elapsed_seconds':time.monotonic()-begin,'last_memory_bytes':memory,'last_disk_bytes':disk})
                write_json(OUT/'report.json',report)
                with (OUT/'resource-samples.jsonl').open('a') as sample:
                    sample.write(json.dumps({'stage':name,'elapsed_seconds':time.monotonic()-started,'cgroup_memory_bytes':memory,'working_disk_bytes':disk,'free_disk_bytes':free})+'\n')
                if disk>=DISK_LIMIT:
                    raise CapacityStop(f'{name}: working disk reached {disk} bytes; cap {DISK_LIMIT} bytes')
                if free<2*GIB:
                    raise CapacityStop(f'{name}: root free disk {free} bytes below 2 GiB reserve')
                if memory is not None and memory>=MEMORY_STOP:
                    raise CapacityStop(f'{name}: aggregate cgroup memory reached {memory} bytes; stop threshold {MEMORY_STOP}')
                if time.monotonic()-started>=TIME_LIMIT:
                    raise CapacityStop(f'{name}: five-hour processing budget exhausted; reserve report/upload time')
                code=proc.poll()
                if code is not None:
                    stage['returncode']=code
                    events=(cg/'memory.events').read_text()
                    if code and ('oom_kill 0' not in events):
                        raise CapacityStop(f'{name}: cgroup 13 GiB memory limit killed the build; {events.strip()}')
                    if code:
                        raise RuntimeError(f'{name} failed with exit {code}; see stage log')
                    stage['status']='complete'
                    break
                time.sleep(2)
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            stage['status']='stopped'
            raise
        finally:
            stage['elapsed_seconds']=time.monotonic()-begin
            # Keep a bounded tail as review evidence; full log stays in Actions.
            with log_path.open('rb') as source:
                source.seek(max(0,log_path.stat().st_size-512_000))
                (OUT/(name+'-log-tail.txt')).write_bytes(source.read())
            print(f'--- {name} final log tail ---',flush=True)
            print((OUT/(name+'-log-tail.txt')).read_text(errors='replace'),flush=True)


def main():
    if os.environ.get('GITHUB_SHA') is None:
        raise SystemExit('Road work runs only through the manual Actions workflow')
    WORK.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
    cg=cgroup_path();started=time.monotonic()
    report={'status':'running','implementation_sha':os.environ['GITHUB_SHA'],
        'stages':[],'peak_cgroup_memory_bytes':0,'peak_working_disk_bytes':0,
        'minimum_free_disk_bytes':os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize,
        'limits':{'cpu':4,'tile_threads':2,'memory_hard_bytes':13*GIB,'working_disk_bytes':DISK_LIMIT,'processing_seconds':TIME_LIMIT},
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
