"""Resource measurements for the free-runner experiments; no road work locally."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from common import ROOT, write_json

WORK=ROOT/'work'
OUT=ROOT/'output'
GIB=1024**3
DISK_RESERVE=10*GIB
TIME_LIMIT=int(os.environ.get('PLOW_PROCESSING_SECONDS','20400'))
DEADLINE=None


class CapacityStop(Exception):
    def __init__(self,message,kind='capacity'):
        self.kind=kind
        super().__init__(message)


def disk_bytes(root=None):
    size=0
    for path in (root or ROOT).rglob('*'):
        try:
            if path.is_file():size+=path.stat().st_blocks*512
        except FileNotFoundError:continue
    return size


def workspace_space():
    value=os.statvfs(ROOT);device=os.stat(ROOT).st_dev
    return {'available_bytes':value.f_bavail*value.f_frsize,'free_bytes':value.f_bfree*value.f_frsize,
            'total_bytes':value.f_blocks*value.f_frsize,'available_inodes':value.f_favail,
            'device_id':f'{os.major(device)}:{os.minor(device)}'}


def filesystem_evidence():
    return {'workspace':str(ROOT),'space':workspace_space(),
        'df_B1':subprocess.check_output(['df','-B1',str(ROOT)],text=True),
        'findmnt':json.loads(subprocess.check_output(['findmnt','--json','--target',str(ROOT),
                                '--output','SOURCE,TARGET,FSTYPE,MAJ:MIN'],text=True))}


def cgroup_path():
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        if line.startswith('0::'):return (Path('/sys/fs/cgroup')/line.split('::',1)[1].lstrip('/')).parent
    raise RuntimeError('cgroup v2 is required')


def read_number(path):
    try:return int(path.read_text().strip())
    except (FileNotFoundError,ValueError):return None


def memory_stat(cg):
    return {key:int(value) for key,value in (line.split() for line in (cg/'memory.stat').read_text().splitlines())
            if key in {'anon','file','kernel','shmem','inactive_file','active_file'}}


def events(cg):
    return {key:int(value) for key,value in (line.split() for line in (cg/'memory.events').read_text().splitlines())}


def process_memory(cg):
    pids=set();rss=swap=0
    for path in cg.rglob('cgroup.procs'):
        try:pids.update(path.read_text().split())
        except FileNotFoundError:continue
    for pid in pids:
        try:
            values={line.split(':',1)[0]:line.split(':',1)[1].strip().split()
                    for line in (Path('/proc')/pid/'status').read_text().splitlines() if ':' in line}
            rss+=int(values.get('VmRSS',['0'])[0])*1024
            swap+=int(values.get('VmSwap',['0'])[0])*1024
        except (FileNotFoundError,ProcessLookupError):continue
    return rss,swap


def verify_memory_bounds(cg,swap_limit=0):
    maximum=read_number(cg/'memory.max');swap=read_number(cg/'memory.swap.max')
    if maximum!=13*GIB or swap!=swap_limit:
        raise RuntimeError('Pilot requires enforced cgroup memory.max=13 GiB and the selected swap bound')
    return {'memory_max_bytes':maximum,'memory_swap_max_bytes':swap}


def reset_peak(path):
    source=None
    try:
        source=path.open('w+');source.write('0');source.flush()
        return source  # Kernel resets apply to reads through this same descriptor.
    except OSError:
        if source is not None:source.close()
        return None


def peak_value(source):
    if source is None:return None
    try:source.seek(0);return int(source.read().strip())
    except (OSError,ValueError):return None


def stop_containers():
    for name in ['plow-osrm','plow-extract','plow-contract']:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)


def run_stage(name,report,cg,started,command=None,context=None):
    context=context or {};space=workspace_space();oom_before=events(cg).get('oom_kill',0)
    stage={'name':name,'status':'running','context':context,'peak_working_disk_bytes':0,
           'minimum_free_disk_bytes':space['available_bytes'],'peak_cgroup_memory_bytes':0,
           'peak_cgroup_swap_bytes':0,'peak_process_rss_bytes':0,'peak_process_swap_bytes':0}
    reset_ram=reset_peak(cg/'memory.peak');reset_swap=reset_peak(cg/'memory.swap.peak')
    stage['kernel_peak_reset']={'ram':reset_ram is not None,'swap':reset_swap is not None}
    report['stages'].append(stage);begin=time.monotonic();last_print=begin-60
    time_path=OUT/(name+'-time.txt')
    env={**os.environ,**{key:str(value) for key,value in context.get('env',{}).items()}}
    proc=subprocess.Popen(['/usr/bin/time','-v','-o',str(time_path),sys.executable,'-u',
        *[str(ROOT/'scripts'/part) if i==0 else part for i,part in enumerate(command or [name+'.py'])]],
        cwd=ROOT,env=env,start_new_session=True)
    try:
        while True:
            memory=read_number(cg/'memory.current') or 0;swap=read_number(cg/'memory.swap.current') or 0
            ram_peak=max(read_number(cg/'memory.peak') or 0,memory);swap_peak=max(read_number(cg/'memory.swap.peak') or 0,swap)
            stage_ram=peak_value(reset_ram);stage_swap=peak_value(reset_swap)
            stage['kernel_peak_reset']={'ram':stage_ram is not None,'swap':stage_swap is not None}
            composition=memory_stat(cg);rss,process_swap=process_memory(cg)
            disk=disk_bytes();space=workspace_space();free=space['available_bytes'];now=time.monotonic()
            if context.get('state'):
                state_bytes=disk_bytes(ROOT/'work'/context['state'])+disk_bytes(OUT/context['state'])
                stage['peak_state_disk_bytes']=max(stage.get('peak_state_disk_bytes',0),state_bytes)
            for key,value in [('peak_cgroup_memory_bytes',ram_peak),('peak_cgroup_swap_bytes',swap_peak),
                              ('peak_process_rss_bytes',rss),('peak_process_swap_bytes',process_swap),
                              ('peak_observed_anon_bytes',composition.get('anon',0)),('peak_working_disk_bytes',disk)]:
                report[key]=max(report.get(key,0),value)
            report['minimum_free_disk_bytes']=min(report['minimum_free_disk_bytes'],free)
            report['last_memory_stat']=composition;report['last_workspace_space']=space
            report['peak_filesystem_used_bytes']=max(report['peak_filesystem_used_bytes'],space['total_bytes']-space['free_bytes'])
            stage.update({'elapsed_seconds':now-begin,'last_memory_bytes':memory,'last_swap_bytes':swap,
                'last_disk_bytes':disk,'last_free_disk_bytes':free,
                'peak_working_disk_bytes':max(stage['peak_working_disk_bytes'],disk),
                'minimum_free_disk_bytes':min(stage['minimum_free_disk_bytes'],free)})
            for key,value in [('peak_cgroup_memory_bytes',max(memory,stage_ram or 0)),
                              ('peak_cgroup_swap_bytes',max(swap,stage_swap or 0)),
                              ('peak_process_rss_bytes',rss),('peak_process_swap_bytes',process_swap)]:
                stage[key]=max(stage[key],value)
            report['elapsed_seconds']=now-started;write_json(OUT/'report.json',report)
            if now-last_print>=60:
                print('Resource checkpoint: '+json.dumps(stage),flush=True);last_print=now
            if free<DISK_RESERVE:raise CapacityStop(f'{name}: free disk below system/packaging reserve','disk')
            if now-started>=TIME_LIMIT or DEADLINE is not None and time.time()>=DEADLINE:
                raise CapacityStop(f'{name}: overnight processing time budget exhausted','time')
            code=proc.poll()
            if code is not None:
                stage['returncode']=code;stage['oom_kills']=events(cg).get('oom_kill',0)-oom_before
                if stage['oom_kills']>0:raise CapacityStop(f'{name}: actual OOM at enforced 13 GiB RAM limit','oom')
                if code:raise RuntimeError(f'{name} failed with exit {code}; inspect logs')
                stage['status']='complete';break
            time.sleep(2)
    except BaseException:
        if proc.poll() is None:
            os.killpg(proc.pid,signal.SIGTERM)
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
        stage['status']='stopped';raise
    finally:
        for source in [reset_ram,reset_swap]:
            if source is not None:source.close()
        stage['elapsed_seconds']=time.monotonic()-begin
        print('Stage outcome: '+json.dumps(stage),flush=True)
        if time_path.exists():print(time_path.read_text(),flush=True)
    return stage
