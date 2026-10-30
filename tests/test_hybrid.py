"""Directed partial-edge searches and composition over exported browser costs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hybrid import Graph, Targets, compose
from routing import accuracy_summary


def segment(forward=True, cost=1000):
    return Graph(np.array([0,1,1] if forward else [0,0,1]),
                 np.array([1] if forward else [0]),np.array([cost]),np.array([[0,0],[1000,0]]))


class Hybrid(unittest.TestCase):
    def test_search_traverses_directed_csr_between_partial_segments(self):
        graph=Graph(np.array([0,1,2,2]),np.array([1,2]),np.array([100,100]),
                    np.array([[0,0],[1000,0],[2000,0]]))
        self.assertEqual(graph.reached_cells(graph.snap([[250,0]])[0],
                         Targets(graph,graph.snap([[1500,0]]))),{0:125})
        self.assertEqual(graph.reached_cells(graph.snap([[1500,0]])[0],
                         Targets(graph,graph.snap([[250,0]]))),{})

    def test_partial_edges_respect_oneway_and_do_not_jump_to_a_vertex(self):
        graph=segment();origin=graph.snap([[250,0]])[0]
        targets=Targets(graph,graph.snap([[100,0],[500,0],[1000,0]]))
        self.assertEqual(graph.reached_cells(origin,targets),{1:250})
        self.assertEqual(graph.reached_cells(graph.snap([[1000,0]])[0],targets),{2:0})
        reverse=segment(False)
        self.assertEqual(reverse.reached_cells(reverse.snap([[750,0]])[0],
                         Targets(reverse,reverse.snap([[250,0],[900,0]]))),{0:500})

    def test_ten_minute_bound_includes_exact_boundary_only(self):
        graph=segment(cost=1200)
        reached=graph.reached_cells(graph.snap([[0,0]])[0],Targets(graph,graph.snap([[500,0],[501,0]])))
        self.assertEqual(reached,{0:600})
        self.assertTrue(all(seconds<=600 for seconds in reached.values()))

    def test_snap_uses_segment_not_nearest_vertex_and_has_distance_limit(self):
        graph=segment()
        self.assertAlmostEqual(graph.snap([[500,100]])[0][1],.5)
        self.assertAlmostEqual(graph.snap([[500,100]])[0][2],100)
        self.assertIsNone(graph.snap([[500,1001]])[0])

    def test_minimum_composition_keeps_course_specific_minima(self):
        tables=[{'minutes':{'a':10,'b':2}},{'minutes':{'a':5,'c':1}}]
        self.assertEqual(compose({0:30,1:120},tables),{'a':420,'b':150,'c':180})

    def test_missing_cell_course_entries_remain_missing_and_fail_accuracy(self):
        estimates=compose({0:10},[{'minutes':{},'matrix_error_course_ids':['a']}])
        self.assertNotIn('a',estimates)
        row={'pairs':[{'reference_status':'within_90','table_minutes':estimates.get('a')}],
             'exact_nearest10':[],'table_nearest10':[]}
        summary=accuracy_summary([row])
        self.assertEqual(summary['missing_table_for_exact_within90'],1)
        self.assertEqual(summary['within_2_minutes_share'],0)

    def test_loader_searches_the_exported_modo_arrays(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);arrays={}
            values={'first_out':([0,1,1],'<u4'),'heads':([1],'<u4'),
                    'weights':([1200],'<f8'),
                    'coordinates_e7':([[410000000,-870000000],[410000000,-869900000]],'<i4')}
            for name,(value,dtype) in values.items():
                array=np.array(value,dtype=dtype);array.tofile(directory/name)
                arrays[name]={'url':name,'dtype':dtype,'length':array.size}
            (directory/'manifest.json').write_text(json.dumps({'arrays':arrays}))
            (directory/'snap_eligible.u8').write_bytes(bytes([1]))
            graph=Graph.load(directory)
            self.assertEqual(graph.forward.tolist(),[1200])
            self.assertTrue(np.isinf(graph.reverse[0]))
            from shapely import get_coordinates
            xy=get_coordinates(graph.lines[0]);middle=(xy[0]+xy[1])/2
            self.assertIsNotNone(graph.snap([middle],True)[0])
            reached=graph.reached_cells(graph.snap([xy[0]])[0],Targets(graph,graph.snap([middle])))
            self.assertAlmostEqual(reached[0],600)
