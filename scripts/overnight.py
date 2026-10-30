"""Sequential Illinois/Texas, no-buffer experiments on one free runner."""
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pilot
from common import ROOT, write_json

OUT=ROOT/'output'
GIB=1024**3


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def accuracy_goal(value):
    return bool(value.get('exact_within_90_pairs',0)>0 and
        (value.get('within_2_minutes_share') or 0)>=.9 and
        (value.get('nearest10_same_order_share') or 0)>.5)


def recommended_method(row):
    methods=[name for name in ['plain','hybrid'] if (row.get(name) or {}).get('exact_within_90_pairs',0)>0]
    passing=[name for name in methods if accuracy_goal(row[name])]
    # A plain lookup needs no local search when both methods meet the goals.
    if passing:return passing[0]
    return max(methods,key=lambda name:((row[name].get('within_2_minutes_share') or 0),
        (row[name].get('nearest10_same_order_share') or 0))) if methods else None


def goal_met(row):
    return bool(row.get('payload_complete') and (row.get('plain') or {}).get('matrix_error_pairs',1)==0 and
        any(accuracy_goal(row.get(name) or {}) for name in ['plain','hybrid']))


def recommended(rows):
    complete=[r for r in rows if r.get('status')=='complete' and r.get('payload_complete') and
        (r.get('plain') or {}).get('matrix_error_pairs',1)==0 and recommended_method(r) is not None]
    passing=[r for r in complete if goal_met(r)]
    if passing:return min(passing,key=lambda r:(r['table_gzip_bytes'],
        0 if recommended_method(r)=='plain' else r['hybrid']['local_runtime_mean_seconds'],r.get('search_seconds',600)))
    return max(complete,key=lambda r:((r[recommended_method(r)].get('within_2_minutes_share') or 0),
        (r[recommended_method(r)].get('nearest10_same_order_share') or 0),-r['table_gzip_bytes'])) if complete else None


def national_estimate(report,spacing):
    anchors=[]
    for state in ['illinois','texas']:
        source=read(OUT/state/'sources.json');table=read(OUT/state/'variants/grid2000-stock/table-manifest.json')
        info=report['states'].get(state,{})
        if not source or not table or not table['payload_complete'] or not info.get('baseline_complete'):continue
        stages=[s for s in report['stages'] if s['context'].get('state')==state and s['context'].get('phase') in {'prepare','clip','extract','contract'}]
        reference=read(OUT/state/'reference-summary.json') or {}
        startup=max(table.get('server_start_seconds',0),reference.get('server_start_seconds',0))
        anchors.append({'state':state,'build_rate':(sum(s['elapsed_seconds'] for s in stages)+startup)/source['input_bytes'],
            'snap_rate':table['snap_seconds']/table['cells'],
            'pair_rate':(table['matrix_seconds']+table['encode_seconds'])/max(1,table['requested_pairs'])})
    if len(anchors)!=2:return {'status':'unavailable','reason':'Both state build/table baselines must complete'}
    inputs=read(OUT/'illinois/national-inputs.json');jobs={}
    image=sum(s['elapsed_seconds'] for s in report['stages'] if s['context'].get('phase')=='image')
    for state in inputs:
        cells=state['area_m2']/spacing**2;pairs=cells*state['coordinate_courses']
        jobs[state['state']]={bound:state['input_bytes']*choose(a['build_rate'] for a in anchors)+
            cells*choose(a['snap_rate'] for a in anchors)+pairs*choose(a['pair_rate'] for a in anchors)+image
            for bound,choose in [('low',min),('high',max)]}
    totals={key:sum(v[key] for v in jobs.values()) for key in ['low','high']}
    return {'status':'planning_proxy','cell_metres':spacing,'anchors':anchors,'runner_hours':{k:v/3600 for k,v in totals.items()},
        'ideal_four_slot_lower_bound_hours':{k:max(totals[k]/4,max(v[k] for v in jobs.values()))/3600 for k in totals},
        'available_concurrency':'Unverified account headroom; this overnight experiment uses one standard job. Four slots are a conditional planning scenario.',
        'qualification':'Zero-buffer OSRM+table generation only. Build rates include any failed pre-swap/tool attempts and successful work: observed cold-run/retry workload, not an ideal successful-build time. IL/TX input-byte, cell-snap and table/encoding-pair rates bracket a planning scenario, not a confidence interval. Area/grid^2 and selected documented course records scale pairs; mapped facilities without course records are omitted. No RAM-fit claim for other states or untested Texas resolutions. Browser export, accuracy evaluation and runner provisioning excluded.'}


def attach_resources(report):
    keys=['peak_cgroup_memory_bytes','peak_cgroup_swap_bytes','peak_process_rss_bytes',
          'peak_process_swap_bytes','peak_state_disk_bytes','peak_working_disk_bytes']
    def aggregate(stages):
        return {**{key:max((s.get(key,0) for s in stages),default=0) for key in keys},
                'elapsed_seconds':sum(s['elapsed_seconds'] for s in stages)}
    for state,info in report['states'].items():
        stages=[s for s in report['stages'] if s['context'].get('state')==state and s['context'].get('variant_id') is None]
        info['resources']=aggregate(stages)
        info['attempts']={label:aggregate([s for s in stages if s['context']['swap_enabled']==enabled])
                          for label,enabled in [('without_swap',False),('with_swap',True)]}
        info['course_coverage']=read(OUT/state/'course-coverage.json') or read(OUT/state/'catalog-counts.json')
        info['table']=read(OUT/state/'variants/grid2000-stock/table-manifest.json')
        info['table_sizes']=read(OUT/state/'variants/grid2000-stock/table-sizes.json')
        browser=read(OUT/state/'browser-sizes.json')
        if browser:
            info['browser_sizes']={key:browser[key] for key in ['vertices','arcs','tile_count']}
            for kind in ['full','tiles']:
                info['browser_sizes'][kind+'_payload']={key:browser[kind][key] for key in ['raw_bytes','gzip_bytes']}
    for row in report['variants']:
        row['resources']=aggregate([s for s in report['stages'] if s['context'].get('variant_id')==row['id']])


def format_accuracy(value):
    if not value:return 'unavailable'
    return '; '.join(f"{value[count]}/{value[denom]} ({100*value[share]:.1f}%)" if value[share] is not None else f"0/{value[denom]} (undefined)"
        for count,denom,share in [('within_1_minute_count','exact_within_90_pairs','within_1_minute_share'),
            ('within_2_minutes_count','exact_within_90_pairs','within_2_minutes_share'),
            ('nearest10_same_order_count','nearest10_eligible_origins','nearest10_same_order_share')])


def report_markdown(report):
    rows=[['Experiment','Result / resources','Table gzip / build s','Plain 1m / 2m / order','Hybrid 1m / 2m / order','Local mean / p95 ms; cells']]
    for state,info in report['states'].items():
        stages=[s for s in report['stages'] if s['context'].get('state')==state and s['context'].get('variant_id') is None]
        ram=max((s['peak_cgroup_memory_bytes'] for s in stages),default=0)/GIB
        swap=max((s['peak_cgroup_swap_bytes'] for s in stages),default=0)/GIB
        rss=max((s['peak_process_rss_bytes'] for s in stages),default=0)/GIB
        rows.append([state+' fit',f"{info.get('fit','blocked')}; cgroup RAM/swap {ram:.3f}/{swap:.3f} GiB; process RSS {rss:.3f} GiB; {info.get('reason','')}",'','','',''])
    for row in report['variants']:
        hybrid=row.get('hybrid')
        local=f"{1000*hybrid['local_runtime_mean_seconds']:.3f}/{1000*hybrid['local_runtime_p95_seconds']:.3f}; {hybrid['reached_cells_mean']:.2f}" if hybrid else ''
        label='drivable pre-snap' if row['snapping']=='drivable' else 'stock'
        rows.append([f"IL {row['cell_metres']}m / {row['search_seconds']//60}m / {label}",
            row['status']+(' / '+row['reason'] if row.get('reason') else ''),
            f"{row.get('table_gzip_bytes','?')} B / {row.get('table_build_seconds','?')}",
            format_accuracy(row.get('plain')),format_accuracy(hybrid),local])
    text='| '+' | '.join(rows[0])+' |\n|'+'---|'*len(rows[0])+'\n'
    text+=''.join('| '+' | '.join(str(v).replace('|','/') for v in row)+' |\n' for row in rows[1:])
    return f"Overnight status: **{report['status']}**\n\n"+text+'\n'+\
        'New fixed 100 origins, seed 20261029, at least 50 km inside the pinned Illinois polygon in EPSG:5070; not the prior unrestricted sample. No failed origins are replaced. The same clipped-state OSRM references and eligible pair/origin sets are reused; not full-network or real-world truth. Missing estimates fail thresholds. Query errors, failed snaps, nulls and missing cells remain in JSON/log coverage. Goals: >=90% within two minutes and >50% nearest-10 orders among eligible origins, complete table payloads only.\n\n'+\
        'Browser graph retains directed static segment costs and service routing arcs; drivable pre-snap excludes service/footpath candidates. Table points then undergo stock OSRM resnapping, so coincident-road class exclusion is not guaranteed. Directed partial edges count; perpendicular matching offsets are reported but excluded from time. Process RSS/swap are sampled cgroup PID sums (shared pages can be counted twice); cgroup RAM includes cache. Stage timings, process/cgroup peaks, disk and exits are in report.json/logs.\n\n'+\
        'National estimate: '+json.dumps(report.get('national_estimate',{}))+'\n'


class Experiments:
    def __init__(self,report,cg,started):
        self.report=report;self.cg=cg;self.started=started;self.counter=0;self.last_kind=None;self.last_reason=None
        self.mode=False;self.retry_used={};self.time_exhausted=False
        self.active_variant=None

    def swap(self,enabled):
        if enabled:
            path=ROOT/'plow.swap'
            if not path.exists():
                if pilot.workspace_space()['available_bytes']<26*GIB:raise pilot.CapacityStop('Insufficient free disk for 16 GiB swap plus reserve','disk')
                subprocess.run(['fallocate','-l','16G',str(path)],check=True);path.chmod(0o600)
                subprocess.run(['mkswap',str(path)],check=True)
            active=subprocess.check_output(['swapon','--show','--noheadings','--raw','--output','NAME'],text=True).splitlines()
            if str(path) not in active:subprocess.run(['swapon',str(path)],check=True)
        else:subprocess.run(['swapoff','-a'],check=True)
        limit='16G' if enabled else '0'
        for unit in ['plow-pilot.slice','plow-pilot.service']:
            subprocess.run(['systemctl','set-property','--runtime',unit,'MemorySwapMax='+limit],check=True)
        pilot.verify_memory_bounds(self.cg,16*GIB if enabled else 0)
        self.mode=enabled
        record={'enabled':enabled,'host_swaps':Path('/proc/swaps').read_text(),'memory_swap_max_bytes':16*GIB if enabled else 0}
        self.report.setdefault('swap_changes',[]).append(record);print('Swap mode: '+json.dumps(record),flush=True)

    def stage(self,state,phase,command,spacing=2000,snapping='stock',search=600):
        if self.time_exhausted:return False
        if time.monotonic()-self.started>=pilot.TIME_LIMIT or pilot.DEADLINE is not None and time.time()>=pilot.DEADLINE:
            self.last_kind='time';self.last_reason='Overnight deadline reached';self.time_exhausted=True;return False
        self.counter+=1
        context={'state':state,'phase':phase,'swap_enabled':self.mode,'cell_metres':spacing,'snapping':snapping,'search_seconds':search,
            'variant_id':self.active_variant,
            'env':{'PLOW_STATE':state,'PLOW_GRID_METRES':spacing,'PLOW_SNAPPING':snapping,'PLOW_SEARCH_SECONDS':search}}
        name=f'{self.counter:02}-{state}-{phase}-{spacing}-{snapping}-{search}'
        try:
            pilot.run_stage(name,self.report,self.cg,self.started,command,context);self.last_kind=None;self.last_reason=None
            return True
        except Exception as error:
            pilot.stop_containers();self.last_kind=error.kind if isinstance(error,pilot.CapacityStop) else 'error'
            self.last_reason=str(error);self.time_exhausted=self.last_kind=='time'
            print('Experiment blocked: '+json.dumps({'stage':name,'kind':self.last_kind,'reason':self.last_reason}),flush=True)
            self.report['stages'][-1]['reason']=self.last_reason
            detail=read(OUT/state/({'extract':'osrm_extract','contract':'osrm_contract'}.get(phase,phase)+'.json'))
            if detail:self.report['stages'][-1]['worker_result']=detail
            return False

    def once(self,state,phase,command,**settings):
        if self.stage(state,phase,command,**settings):return True
        if self.last_kind=='error' and phase in {'prepare','image'}:
            return self.stage(state,phase,command,**settings)
        if self.last_kind!='oom' or self.retry_used.get(state):return False
        self.retry_used[state]=True
        try:self.swap(True)
        except Exception as error:self.last_reason=str(error);self.last_kind='swap_unavailable';return False
        self.report['states'][state]['mode']=True
        if phase=='browser_graph':shutil.rmtree(OUT/state/'browser',ignore_errors=True)
        if phase in {'extract','contract'}:
            for path in (ROOT/'work'/state).glob('roads.osrm*'):path.unlink()
        if phase=='contract' and not self.stage(state,'extract',['osrm_build.py','extract']):return False
        return self.stage(state,phase,command,**settings)

    def build(self,state):
        self.active_variant=None
        pilot.stop_containers();self.swap(False)
        info=self.report['states'][state]={'fit':'blocked','baseline_complete':False}
        for phase in ['prepare','clip']:
            if not self.once(state,phase,[('prepare.py' if phase=='prepare' else 'osrm_build.py'),*([] if phase=='prepare' else ['clip'])]):
                info['reason']=self.last_reason;return False
        if state=='illinois':
            info['browser_complete']=self.once(state,'browser_graph',['browser_graph.py'])
        if not self.report.get('image_pulled'):
            if not self.once(state,'image',['osrm_build.py','image']):info['reason']=self.last_reason;return False
            self.report['image_pulled']=True
        if not self.once(state,'extract',['osrm_build.py','extract']):info['reason']=self.last_reason;return False
        if not self.once(state,'contract',['osrm_build.py','contract']):info['reason']=self.last_reason;return False
        info['network_complete']=True;info['mode']=self.mode
        if state=='illinois':info['reference_complete']=self.once(state,'reference',['routing.py','reference'])
        if not self.once(state,'table',['routing.py','table']):info['reason']=self.last_reason;return False
        manifest=read(OUT/state/'variants/grid2000-stock/table-manifest.json')
        info['baseline_complete']=bool(manifest and manifest['payload_complete'])
        info['fit']='with_swap' if self.mode else 'without_swap'
        if not info['baseline_complete']:info['reason']='Table query errors; payload is incomplete'
        info['mode']=self.mode
        return True

    def evaluate(self,spacing,search,snapping='stock',new_table=False):
        self.active_variant=f'{spacing}-{snapping}-{search}'
        row={'id':self.active_variant,'cell_metres':spacing,'search_seconds':search,'snapping':snapping,'status':'blocked'}
        self.report['variants'].append(row);info=self.report['states'].get('illinois',{})
        if not info.get('network_complete') or not info.get('reference_complete') or not info.get('browser_complete'):
            row['reason']='Illinois network, fixed references or browser export unavailable';return row
        if new_table and not self.once('illinois','table',['routing.py','table'],spacing=spacing,snapping=snapping):
            row['reason']=self.last_reason;return row
        directory=OUT/'illinois/variants'/f'grid{spacing}-{snapping}'
        table=read(directory/'table-manifest.json');sizes=read(directory/'table-sizes.json')
        if not table or not sizes:row['reason']='Table variant unavailable';return row
        row.update({'payload_complete':table['payload_complete'],'table_gzip_bytes':sizes['total_gzip_bytes'],
            'table_raw_bytes':sizes['total_raw_bytes'],'table_build_seconds':table['table_seconds'],
            'new_table_build':new_table,'table_coverage':table,'swap_enabled':self.mode})
        pilot.stop_containers()  # Scoring uses stored references and the CSR, not the OSRM server.
        if not (directory/'accuracy.json').exists():
            if not self.once('illinois','plain_score',['routing.py','score'],spacing=spacing,snapping=snapping):
                row['reason']=self.last_reason;return row
        row['plain']=read(directory/'accuracy.json')
        if not self.once('illinois','hybrid',['hybrid.py'],spacing=spacing,snapping=snapping,search=search):
            row['reason']=self.last_reason;return row
        row['hybrid']=read(directory/f'search{search}/hybrid-accuracy.json')
        row['status']='complete' if row['payload_complete'] else 'incomplete_payload'
        if not row['payload_complete']:row['reason']='Table query errors; size is not comparable'
        row['swap_enabled']=self.mode;row['goal_met']=goal_met(row)
        write_json(OUT/'variants.json',self.report['variants'])
        print('Variant result: '+json.dumps(row),flush=True)
        return row

    def run(self):
        self.build('illinois');self.evaluate(2000,600)
        self.report['states']['illinois']['mode']=self.mode
        pilot.stop_containers()
        if not self.time_exhausted:self.build('texas')
        pilot.stop_containers()
        shutil.rmtree(ROOT/'work/texas',ignore_errors=True)  # TX metrics/tables remain; unchanged IL build is retained.
        il_info=self.report['states'].get('illinois',{})
        if il_info.get('network_complete'):
            self.swap(il_info.get('mode',False))
        for spacing in [1000,500]:self.evaluate(spacing,600,new_table=True)
        for spacing in [2000,1000,500]:
            for search in [900,1200]:self.evaluate(spacing,search)
        for spacing in [1000,500]:self.evaluate(spacing,600,'drivable',new_table=True)
        chosen=recommended(self.report['variants'])
        self.report['recommended']=None if chosen is None else {
            'method':recommended_method(chosen),
            'cell_metres':chosen['cell_metres'],'search_seconds':chosen['search_seconds'],'snapping':chosen['snapping'],
            'goal_met':goal_met(chosen),'table_gzip_bytes':chosen['table_gzip_bytes'],
            'qualification':'Smallest complete payload meeting both goals for plain or hybrid; prefer plain when both pass on that payload. Otherwise best tested accuracy, target not met. Plain needs no local search; its row search budget applies only to the companion hybrid test. Same benchmark used for selection, not independent validation.'}
        self.report['national_estimate']=national_estimate(self.report,chosen['cell_metres'] if chosen else 2000)
        attach_resources(self.report)


def main():
    if not os.environ.get('GITHUB_SHA'):raise SystemExit('Road work runs only through the manual Actions workflow')
    OUT.mkdir(exist_ok=True);(ROOT/'work').mkdir(exist_ok=True)
    value=os.environ.get('PLOW_DEADLINE_UTC')
    if value:pilot.DEADLINE=datetime.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    cg=pilot.cgroup_path();started=time.monotonic();initial=pilot.filesystem_evidence()
    report={'status':'running','implementation_sha':os.environ['GITHUB_SHA'],'states':{},'variants':[],'stages':[],
        'initial_workspace_space':initial['space'],'filesystem_evidence':initial,'minimum_free_disk_bytes':initial['space']['available_bytes'],
        'peak_filesystem_used_bytes':initial['space']['total_bytes']-initial['space']['free_bytes'],
        'limits':{'cpu_quota_cores':4,'ram_bytes':13*GIB,'optional_swap_bytes':16*GIB,'processing_seconds':pilot.TIME_LIMIT,'deadline_utc':value},
        'measurement':'Cgroup RAM includes cache; swap measured separately. Process RSS/VmSwap sum is sampled every 2s over cgroup PIDs; shared RSS can be counted twice. Kernel peak used per stage when reset supported, otherwise sampled stage peak.',
        'scope':'Illinois/Texas only, buffer 0, one free standard public Ubuntu job, fixed 100 new interior Illinois origins; no nationwide execution'}
    print('Initial filesystem: '+json.dumps(initial),flush=True)
    code=0
    try:
        pilot.verify_memory_bounds(cg)
        memory={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:','SwapTotal:'))}
        report['host_memory']=memory
        report['host_cpu_count']=os.cpu_count()
        if memory['MemTotal']<15*GIB:raise RuntimeError('Expected the free standard 16 GB runner')
        Experiments(report,cg,started).run()
        report['status']='complete' if report['variants'] and all(v['status']=='complete' for v in report['variants']) and len(report['states'])==2 and all(s.get('baseline_complete') for s in report['states'].values()) else 'partial'
    except Exception as error:report['status']='error';report['error']=f'{type(error).__name__}: {error}';code=1
    finally:
        pilot.stop_containers();report['elapsed_seconds']=time.monotonic()-started
        attach_resources(report)
        report['cgroup_events']=(cg/'memory.events').read_text();report['final_workspace_space']=pilot.workspace_space()
        write_json(OUT/'report.json',report);(OUT/'report.md').write_text(report_markdown(report))
        write_json(OUT/'completion.json',{'status':report['status'],'implementation_sha':report['implementation_sha']})
        print('Overnight final report: '+json.dumps(report),flush=True)
        print((OUT/'report.md').read_text(),flush=True)
    return code


if __name__=='__main__':sys.exit(main())
