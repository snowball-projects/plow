"""Exercise the GeoJSON envelope against Ubuntu's actual Osmium parser in CI."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from geometry import geojson_feature


@unittest.skipUnless(shutil.which('osmium'), 'Osmium integration runs on Ubuntu CI')
class Clipping(unittest.TestCase):
    def test_feature_clip_keeps_complete_ways_and_readable_grid_geometry(self):
        from shapely.geometry import shape, Point
        geometry={'type':'Polygon','coordinates':[[[-.1,-.1],[.1,-.1],[.1,.1],[-.1,.1],[-.1,-.1]]]}
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            polygon=directory/'state.geojson'
            polygon.write_text(json.dumps(geojson_feature(geometry)))
            self.assertTrue(shape(json.loads(polygon.read_text())['geometry']).contains(Point(0,0)))
            source=directory/'fixture.osm'
            source.write_text('<osm version="0.6"><node id="1" lat="0" lon="0"/>'
                              '<node id="2" lat="1" lon="1"/>'
                              '<way id="1"><nd ref="1"/><nd ref="2"/>'
                              '<tag k="highway" v="residential"/></way></osm>')
            output=directory/'clipped.opl'
            subprocess.run(['osmium','extract','--strategy=complete_ways','-p',str(polygon),
                            str(source),'-o',str(output)],check=True,capture_output=True,text=True)
            objects=output.read_text().splitlines()
            self.assertEqual([line.split()[0] for line in objects],['n1','n2','w1'])
