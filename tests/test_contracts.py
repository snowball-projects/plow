import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from common import nearest10, whole_minutes
from prepare import select_courses
from routing import matrix, accuracy_summary, cell_key, centre
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

    def test_asymmetric_matrix_is_origin_to_course_and_keeps_null(self):
        class DirectedActor:
            def sources_to_targets(self, req):
                self.request=req
                # Fixture's forward travel is 89 seconds, reverse is 901.
                self.forward=req['sources'][0]['lat']==41 and req['targets'][0]['lat']==42
                return {'sources_to_targets': [[{'time':89 if self.forward else 901}, {'time':None}]]}
        actor=DirectedActor()
        courses=[{'course_id':'a','destination':{'latitude':42,'longitude':-88}},
                 {'course_id':'b','destination':{'latitude':43,'longitude':-89}}]
        self.assertEqual(matrix(actor,(41,-87),courses),{'a':89,'b':None})
        self.assertEqual(actor.request['costing_options']['auto']['speed_types'],['freeflow'])
        self.assertNotIn('date_time',actor.request)

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


if __name__=='__main__': unittest.main()
