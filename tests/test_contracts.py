import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from common import nearest10, whole_minutes, retained_minutes
from prepare import select_courses
from routing import accuracy_summary
from geometry import cell_key, centre, cells
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

    def test_directed_osrm_indices_nulls_and_no_fallback(self):
        from osrm import Client
        class Response:
            status_code=200
            def json(self):return {'code':'Ok','durations':[[89,None],[901,60]]}
        class Session:
            def get(self,url,params,timeout):
                self.url,self.params=url,params
                return Response()
        session=Session();client=Client(session)
        sources=[{'point':(-87,41)},{'point':(-87,40)}]
        targets=[{'point':(-88,42)},{'point':(-89,43)}]
        self.assertEqual(client.table(sources,targets),[[89,None],[901,60]])
        self.assertEqual(session.params['sources'],'0;1')
        self.assertEqual(session.params['destinations'],'2;3')
        self.assertTrue(session.url.endswith('-87,41;-87,40;-88,42;-89,43'))
        self.assertNotIn('fallback_speed',session.params)
        self.assertEqual(session.params['radiuses'],'1000;1000;1000;1000')

    def test_grid_centres_are_inside_polygon(self):
        from shapely.geometry import Polygon,Point
        polygon=Polygon([(0,0),(6000,0),(6000,2000),(2000,2000),(2000,6000),(0,6000)])
        keys=cells(polygon)
        self.assertEqual(len(keys),5)
        self.assertNotIn((2,2),keys)
        self.assertTrue(all(polygon.contains(Point(*centre(key))) for key in keys))

    def test_cutoff_precedes_rounding(self):
        self.assertIsNone(retained_minutes(5400.1))
        self.assertEqual(retained_minutes(5400),90)
        self.assertEqual(retained_minutes(0),0)
        self.assertIsNone(retained_minutes(None))

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
