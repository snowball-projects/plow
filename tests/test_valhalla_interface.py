"""Dependency smoke check without downloading or building any road graph."""
import tempfile
import unittest

try:
    import valhalla
except ImportError:
    valhalla = None


@unittest.skipUnless(valhalla is not None, 'Valhalla dependency not installed')
class ValhallaInterface(unittest.TestCase):
    def test_empty_graph_locate_and_matrix_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            config = valhalla.get_config(tile_dir=directory, tile_extract='')
            actor = valhalla.Actor(config)
            self.assertTrue(callable(actor.matrix))
            locations = actor.locate({'locations':[{'lat':41,'lon':-87,'search_cutoff':1000,
                                                     'minimum_reachability':0,'radius':0}],
                                      'costing':'auto'})
            self.assertEqual(locations[0]['edges'], [])
