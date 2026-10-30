import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from common import nearest10, whole_minutes
from prepare import select_courses
from routing import course_matrix, matrix_times, origin_matrices, accuracy_summary, cell_key, centre
from browser_graph import directions, speed


class Contracts(unittest.TestCase):
    def test_published_projection_coordinates_are_retained_and_missing_stay_missing(self):
        point={'kind':'property_point','latitude':41.123456789,'longitude':-87.987654321}
        catalog={'destinations':[{'id':'f1','scope':'facility_estimate','precision':'property_point'}],
                 'courses':[{'course_id':'c1','name':'Published Name','state':'IL','map_location':point,'destination_id':'f1'},
                            {'course_id':'c2','state':'IL','destination_id':'f1'},
                            {'course_id':'c3','state':'IN','map_location':point,'destination_id':'f1'}]}
        rows=select_courses(catalog)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['name'],'Published Name')
        self.assertEqual(rows[0]['map_location'],point)
        self.assertEqual(rows[0]['destination']['latitude'],point['latitude'])
        self.assertEqual(rows[0]['destination']['scope'],'facility_estimate')
        self.assertIsNone(rows[1]['destination'])
        self.assertEqual(rows[1]['status'],'arrival_unavailable')

    def test_reverse_matrix_sources_remain_cells_single_target_course(self):
        class DirectedActor:
            def matrix(self, req):
                self.request = req
                # Asymmetric forward durations: 89/90 seconds, reverse 901.
                return {'algorithm':'timedistancematrix', 'sources_to_targets':
                        [[{'time':89 if p['lat']==41 else None}] for p in req['sources']]}
        actor = DirectedActor()
        course = {'course_id':'a','destination':{'latitude':42,'longitude':-88}}
        self.assertEqual(course_matrix(actor, [(41,-87),(40,-87)], course), [89,None])
        self.assertEqual(len(actor.request['sources']),2)
        self.assertEqual(actor.request['sources'][0]['lat'],41)
        self.assertEqual(actor.request['targets'],[{'lat':42.0,'lon':-88.0,'search_cutoff':1000,'minimum_reachability':0,'radius':0}])
        self.assertNotIn('matrix_locations',actor.request)
        self.assertNotIn('date_time',actor.request)

    def test_112_origins_make_112_one_to_many_calls(self):
        class Actor:
            def __init__(self): self.requests=[]
            def matrix(self, request):
                self.requests.append(request)
                return {'algorithm':'timedistancematrix', 'sources_to_targets':[[{'time':60},{'time':None}]]}
        origins=[{'name':str(i),'latitude':41+i/1000,'longitude':-88} for i in range(112)]
        actor=Actor(); results=list(origin_matrices(actor,origins,[(42,-88),(43,-89)]))
        self.assertEqual(len(results),112)
        self.assertEqual(len(actor.requests),112)
        self.assertTrue(all(len(req['sources'])==1 and len(req['targets'])==2 for req in actor.requests))
        self.assertEqual(results[-1][1],[60,None])

    def test_default_costmatrix_response_is_rejected(self):
        class WrongAlgorithm:
            def matrix(self, req):
                return {'algorithm':'costmatrix','sources_to_targets':[[{'time':89}]]}
        with self.assertRaisesRegex(ValueError,'Expected timedistancematrix'):
            matrix_times(WrongAlgorithm(),[(41,-87)],[(42,-88)])

    def test_config_explicitly_selects_time_distance_and_pairs_not_nearest_n(self):
        import tempfile
        from build_tiles import routing_config
        with tempfile.TemporaryDirectory() as path:
            config = routing_config(path)
        self.assertEqual(config['thor']['source_to_target_algorithm'],'timedistancematrix')
        self.assertEqual(config['service_limits']['auto']['max_matrix_location_pairs'],2500)

    def test_illinois_centres_exclude_polygon_holes_and_bounding_box(self):
        try:
            from shapely.geometry import Polygon, Point
        except ImportError:
            self.skipTest('geometry dependency absent locally; required in CI')
        from routing import illinois_cells
        polygon=Polygon([(0,0),(6000,0),(6000,2000),(2000,2000),(2000,6000),(0,6000)])
        cells=illinois_cells(polygon)
        self.assertEqual(len(cells),5)
        self.assertNotIn((2,2),cells)
        self.assertTrue(all(polygon.contains(Point(*centre(key))) for key in cells))

    def test_whole_minutes_and_tie_order(self):
        self.assertEqual([whole_minutes(x) for x in [0,29.9,30,89.9,90,5400]],[0,0,1,1,2,90])
        for x in [math.inf,math.nan,-1]:
            with self.assertRaises(ValueError): whole_minutes(x)
        self.assertEqual(nearest10({'b':1,'a':1,'c':0}),['c','a','b'])

    def test_accuracy_missing_table_is_failure_and_top10_order_is_strict(self):
        rows=[{'pairs':[{'reference_status':'within_90','table_minutes':None},
                        {'reference_status':'within_90','table_minutes':5,'absolute_error_minutes':1.5},
                        {'reference_status':'over_90','table_minutes':90}],
               'exact_nearest10':list('abcdefghij'),'table_nearest10':list('bacdefghij')}]
        result=accuracy_summary(rows)
        self.assertEqual(result['exact_within_90_pairs'],2)
        self.assertEqual(result['within_1_minute_share'],0)
        self.assertEqual(result['within_2_minutes_share'],0.5)
        self.assertEqual(result['missing_table_for_exact_within90'],1)
        self.assertEqual(result['nearest10_same_order_share'],0)
        self.assertIsNone(accuracy_summary([])['nearest10_same_order_share'])

    def test_grid_negative_coordinates_are_floor_based(self):
        self.assertEqual(cell_key(-1,1999),(-1,0))
        self.assertEqual(centre((-1,0)),(-1000,1000))

    def test_browser_oneway_and_motorcar_access(self):
        self.assertEqual(directions({'highway':'residential','oneway':'-1'}),['backward'])
        self.assertEqual(directions({'highway':'residential','junction':'roundabout'}),['forward'])
        self.assertEqual(directions({'highway':'service','access':'private'}),[])
        self.assertEqual(directions({'highway':'service','access':'private','motorcar':'yes'}),['forward','backward'])
        self.assertAlmostEqual(speed({'highway':'primary','maxspeed':'55 mph'},'forward'),88.51392)

class CoverageReporting(unittest.TestCase):
    def test_failed_snaps_and_matrix_errors_remain_visible_beside_conditional_accuracy(self):
        rows=[{'origin_snap':{'status':'snapped'},'table_status':'cell_missing',
               'pairs':[{'course_id':'a','reference_status':'within_90','table_minutes':None},
                        {'course_id':'b','reference_status':'matrix_error','table_minutes':None}],
               'exact_nearest10':['a'],'table_nearest10':[]},
              {'origin_snap':{'status':'snap_failed'},'table_status':'snap_failed',
               'pairs':[{'course_id':'a','reference_status':'origin_snap_failed','table_minutes':None}],
               'exact_nearest10':[],'table_nearest10':[]}]
        result=accuracy_summary(rows)
        self.assertEqual(result['origin_snap_failed'],1)
        self.assertEqual(result['origin_snap_succeeded'],1)
        self.assertEqual(result['matrix_error_pairs'],1)
        self.assertEqual(result['origin_snap_failed_pairs'],1)
        self.assertEqual(result['origins_with_exact_within90'],1)
        self.assertEqual(result['origins_without_exact_within90'],1)
        self.assertEqual(result['exact_within_90_pairs'],1)
        self.assertEqual(result['courses_with_exact_within90'],1)
        self.assertEqual(result['missing_table_for_exact_within90'],1)
        self.assertEqual(result['within_2_minutes_share'],0)


if __name__=='__main__': unittest.main()
