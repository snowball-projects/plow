"""Fixed references, candidate-only snapping, and bounded overnight selection."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from shapely.geometry import Point, Polygon

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from geometry import origins, cells, centre, transforms, cell_key
from hybrid import Graph, Targets
import routing
import overnight


class Overnight(unittest.TestCase):
    def test_interior_origins_are_deterministic_and_fifty_km_inside(self):
        polygon=Polygon([(0,0),(300000,0),(300000,300000),(0,300000)])
        first=origins(polygon,lambda x,y:(x,y),50000)
        self.assertEqual(first,origins(polygon,lambda x,y:(x,y),50000))
        self.assertEqual(len(first['origins']),100)
        self.assertGreaterEqual(first['minimum_border_distance_metres'],50000)
        self.assertTrue(all(polygon.boundary.distance(Point(o['longitude'],o['latitude']))>=50000 for o in first['origins']))

    def test_all_grid_sizes_keep_strictly_inside_centres(self):
        polygon=Polygon([(0,0),(4000,0),(4000,4000),(0,4000)])
        for spacing,count in [(2000,4),(1000,16),(500,64)]:
            grid=cells(polygon,spacing)
            self.assertEqual(len(grid),count)
            self.assertTrue(all(polygon.contains(Point(*centre(key,spacing))) for key in grid))

    def test_service_candidate_exclusion_preserves_service_routing(self):
        graph=Graph(np.array([0,1,2,2,3]),np.array([1,2,2]),np.array([10,10,100]),
            np.array([[0,0],[1000,0],[1000,100],[0,100]]),np.array([0,1,1],dtype=np.uint8))
        ordinary=graph.snap([[500,0]])[0];better=graph.snap([[500,0]],True)[0]
        self.assertAlmostEqual(graph.position(ordinary)[1],0)
        self.assertAlmostEqual(graph.position(better)[1],100)
        self.assertAlmostEqual(better[2],100)
        self.assertEqual(graph.reached_cells(graph.snap([[250,0]])[0],Targets(graph,graph.snap([[750,0]]))),{0:5})
        self.assertEqual(len(graph.heads),3)

    def test_plain_variants_keep_all_origins_and_reference_denominators(self):
        to_xy,_,_=transforms();lon,lat=-89,40;xy=to_xy(lon,lat)
        reference={'seed':20261029,'origins':[{'name':f'random-{i:03}','longitude':lon,'latitude':lat,
            'origin_snap':{'status':'snapped'},'exact_nearest10':['a','b'],'table_nearest10':[],
            'pairs':[{'course_id':cid,'reference_status':'within_90','reference_seconds':seconds,'table_minutes':None}
                     for cid,seconds in [('a',60),('b',120)]]} for i in range(100)]}
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary);path=out/'reference-origins.json';path.write_text(json.dumps(reference));frozen=path.read_bytes()
            summaries=[]
            for spacing,minutes in [(2000,{'a':1,'b':2}),(500,{'a':1})]:
                variant=out/str(spacing);(variant/'tables').mkdir(parents=True)
                (variant/'tables/area.jsonl').write_text(json.dumps({'cell':cell_key(*xy,spacing),'status':'snapped','minutes':minutes})+'\n')
                with patch.multiple(routing,OUT=out,TABLE_OUT=variant,SPACING=spacing):routing.score_plain()
                summaries.append(json.loads((variant/'accuracy.json').read_text()))
                self.assertEqual(path.read_bytes(),frozen)
            self.assertEqual([s['origins'] for s in summaries],[100,100])
            self.assertEqual([s['exact_within_90_pairs'] for s in summaries],[200,200])
            self.assertEqual(summaries[1]['missing_table_for_exact_within90'],100)
            self.assertEqual(summaries[1]['within_2_minutes_share'],.5)

    def test_goals_require_most_orders_and_complete_payload(self):
        row={'status':'complete','payload_complete':True,'plain':{'matrix_error_pairs':0},
             'hybrid':{'exact_within_90_pairs':10,'within_2_minutes_share':.9,'nearest10_same_order_share':.5,
                       'local_runtime_mean_seconds':1},'table_gzip_bytes':100}
        self.assertFalse(overnight.goal_met(row))
        row['hybrid']['nearest10_same_order_share']=.6
        self.assertTrue(overnight.goal_met(row))
        tiny={**row,'payload_complete':False,'table_gzip_bytes':1}
        self.assertIs(overnight.recommended([tiny,row]),row)
        row['plain']['matrix_error_pairs']=1
        self.assertFalse(overnight.goal_met(row))

    def test_only_one_oom_swap_fallback_per_state(self):
        report={'states':{'illinois':{},'texas':{}},'stages':[]}
        experiments=overnight.Experiments(report,Mock(),0)
        experiments.last_kind='oom'
        with patch.object(experiments,'stage',side_effect=[False,True,False,False,True]),patch.object(experiments,'swap') as swap:
            self.assertTrue(experiments.once('illinois','table',['routing.py','table']))
            experiments.last_kind='oom'
            self.assertFalse(experiments.once('illinois','table',['routing.py','table']))
            experiments.last_kind='oom'
            self.assertTrue(experiments.once('texas','table',['routing.py','table']))
            self.assertEqual(swap.call_count,2)

    def test_national_projection_uses_two_no_buffer_anchors_and_own_state_sums(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary)
            for state,input_bytes,snap,matrix,cells_count,pairs in [('illinois',100,10,90,10,100),('texas',200,40,100,20,200)]:
                (out/state/'variants/grid2000-stock').mkdir(parents=True)
                (out/state/'sources.json').write_text(json.dumps({'input_bytes':input_bytes}))
                (out/state/'variants/grid2000-stock/table-manifest.json').write_text(json.dumps({
                    'payload_complete':True,'snap_seconds':snap,'matrix_seconds':matrix,'encode_seconds':0,
                    'cells':cells_count,'requested_pairs':pairs}))
            (out/'illinois/national-inputs.json').write_text(json.dumps([
                {'state':'a','area_m2':40e6,'coordinate_courses':10,'input_bytes':100},
                {'state':'b','area_m2':80e6,'coordinate_courses':10,'input_bytes':200}]))
            report={'states':{s:{'baseline_complete':True} for s in ['illinois','texas']},'stages':[
                {'context':{'state':'illinois','phase':'prepare'},'elapsed_seconds':100},
                {'context':{'state':'texas','phase':'prepare'},'elapsed_seconds':400}]}
            with patch.object(overnight,'OUT',out):value=overnight.national_estimate(report,2000)
            self.assertAlmostEqual(value['runner_hours']['low'],480/3600)
            self.assertAlmostEqual(value['runner_hours']['high'],930/3600)
            self.assertAlmostEqual(value['ideal_four_slot_lower_bound_hours']['low'],320/3600)
            self.assertAlmostEqual(value['ideal_four_slot_lower_bound_hours']['high'],620/3600)

    def test_sequence_reuses_illinois_build_and_freezes_reference_before_variants(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary);report={'states':{},'variants':[],'stages':[]}
            experiments=overnight.Experiments(report,Mock(),0);calls=[]
            accuracy={'origins':100,'matrix_error_pairs':0,'exact_within_90_pairs':100,
                'within_1_minute_count':95,'within_2_minutes_count':95,'within_1_minute_share':.95,'within_2_minutes_share':.95,
                'nearest10_eligible_origins':10,'nearest10_same_order_count':6,'nearest10_same_order_share':.6,
                'local_runtime_mean_seconds':.1,'local_runtime_p95_seconds':.2,'reached_cells_mean':5}
            def stage(state,phase,command,spacing=2000,snapping='stock',search=600):
                calls.append((state,phase,spacing,snapping,search));directory=out/state/'variants'/f'grid{spacing}-{snapping}'
                directory.mkdir(parents=True,exist_ok=True)
                report['stages'].append({'name':str(len(calls)),'context':{'state':state,'phase':phase,
                    'variant_id':experiments.active_variant,'swap_enabled':False},'elapsed_seconds':1})
                if phase=='prepare':
                    (out/state/'sources.json').write_text(json.dumps({'input_bytes':100}))
                    (out/state/'national-inputs.json').write_text(json.dumps([
                        {'state':'illinois','area_m2':4e6,'coordinate_courses':1,'input_bytes':100},
                        {'state':'texas','area_m2':8e6,'coordinate_courses':1,'input_bytes':100}]))
                if phase=='table':
                    (directory/'table-manifest.json').write_text(json.dumps({'payload_complete':True,'table_seconds':4,
                        'snap_seconds':1,'matrix_seconds':2,'encode_seconds':1,'cells':1,'requested_pairs':1}))
                    (directory/'table-sizes.json').write_text(json.dumps({'total_gzip_bytes':10,'total_raw_bytes':100}))
                if phase=='plain_score':(directory/'accuracy.json').write_text(json.dumps(accuracy))
                if phase=='hybrid':
                    (directory/f'search{search}').mkdir(exist_ok=True)
                    (directory/f'search{search}/hybrid-accuracy.json').write_text(json.dumps(accuracy))
                return True
            with patch.object(overnight,'OUT',out),patch.object(overnight.pilot,'stop_containers'),patch.object(overnight.shutil,'rmtree'), \
                 patch.object(experiments,'stage',side_effect=stage),patch.object(experiments,'swap',side_effect=lambda enabled:setattr(experiments,'mode',enabled)):
                experiments.run()
            self.assertEqual(sum(s=='illinois' and p=='extract' for s,p,*_ in calls),1)
            self.assertEqual(sum(s=='illinois' and p=='reference' for s,p,*_ in calls),1)
            first_hybrid=next(i for i,c in enumerate(calls) if c[:2]==('illinois','hybrid'))
            texas=next(i for i,c in enumerate(calls) if c[:2]==('texas','prepare'))
            finer=next(i for i,c in enumerate(calls) if c[:3]==('illinois','table',1000))
            self.assertLess(first_hybrid,texas);self.assertLess(texas,finer)
            self.assertEqual(len(report['variants']),10)
            self.assertTrue(all(row['status']=='complete' for row in report['variants']))

    def test_failed_download_is_not_reused_as_shared_input(self):
        import io
        from common import download
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'index.json'
            with patch('urllib.request.urlopen',return_value=io.BytesIO(b'bad')):
                with self.assertRaisesRegex(ValueError,'checksum mismatch'):download('https://example.invalid',path,'0'*64)
            self.assertFalse(path.exists())
            with patch('urllib.request.urlopen',return_value=io.BytesIO(b'{}')):
                download('https://example.invalid',path)
            self.assertEqual(path.read_bytes(),b'{}')

    def test_empty_course_cache_cannot_make_a_zero_work_fit(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary);(out/'course-snaps.json').write_text('[]')
            client=Mock()
            with patch.object(routing,'OUT',out):
                with self.assertRaisesRegex(RuntimeError,'No snapped course'):routing.courses_for(client)
            client.nearest.assert_not_called()

    def test_browser_oom_retry_removes_only_partial_generated_browser_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary);browser=out/'illinois/browser'
            report={'states':{'illinois':{}},'stages':[]};runner=overnight.Experiments(report,Mock(),0)
            sentinel=out/'illinois/reference-origins.json';sentinel.parent.mkdir(parents=True);sentinel.write_text('keep')
            attempts=[]
            def stage(*args,**kwargs):
                attempts.append(1)
                if len(attempts)==1:
                    (browser/'full').mkdir(parents=True);(browser/'full/partial.bin').write_text('partial')
                    runner.last_kind='oom';return False
                self.assertFalse(browser.exists())
                self.assertEqual(sentinel.read_text(),'keep')
                browser.mkdir();return True
            with patch.object(overnight,'OUT',out),patch.object(runner,'stage',side_effect=stage),patch.object(runner,'swap'):
                self.assertTrue(runner.once('illinois','browser_graph',['browser_graph.py']))
            self.assertEqual(len(attempts),2)
            self.assertEqual(sentinel.read_text(),'keep')
