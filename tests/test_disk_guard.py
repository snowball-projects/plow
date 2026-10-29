"""Exercise both sides of the filesystem-space guard through the supervisor."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pilot


class DiskGuard(unittest.TestCase):
    def exercise(self, free, allocated):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, out, cg = (root / name for name in ['work', 'output', 'cg'])
            for path in [work, out, cg]: path.mkdir()
            for name in ['memory.current', 'memory.peak']: (cg / name).write_text('0')
            (cg / 'memory.events').write_text('oom_kill 0\n')
            process = Mock(pid=123)
            process.poll.return_value = 0
            space = {'available_bytes':free, 'free_bytes':free,
                     'total_bytes':100*pilot.GIB, 'available_inodes':1_000_000,
                     'device_id':'8:1'}
            report = {'stages':[], 'peak_cgroup_memory_bytes':0,
                      'peak_working_disk_bytes':0, 'minimum_free_disk_bytes':free,
                      'peak_filesystem_used_bytes':0}
            with patch.multiple(pilot, ROOT=root, WORK=work, OUT=out), \
                 patch.object(pilot, 'workspace_space', return_value=space), \
                 patch.object(pilot, 'disk_bytes', return_value=allocated), \
                 patch.object(pilot.subprocess, 'Popen', return_value=process):
                pilot.run_stage('fixture', report, cg, pilot.time.monotonic())
            return report

    def test_working_set_above_old_cap_continues_with_actual_free_space(self):
        report = self.exercise(free=73*pilot.GIB, allocated=20*pilot.GIB)
        self.assertEqual(report['stages'][0]['status'], 'complete')
        self.assertEqual(report['peak_working_disk_bytes'], 20*pilot.GIB)
        self.assertEqual(report['minimum_free_disk_bytes'], 73*pilot.GIB)

    def test_small_working_set_stops_when_filesystem_needs_reserve(self):
        with self.assertRaisesRegex(pilot.CapacityStop, 'system/packaging reserve'):
            self.exercise(free=9*pilot.GIB, allocated=pilot.GIB)

    def test_filesystem_evidence_has_device_and_available_space(self):
        result = pilot.filesystem_evidence()
        self.assertGreater(result['space']['available_bytes'], 0)
        self.assertIn('device_id', result['space'])
        self.assertIn('Filesystem', result['df_B1'])
        self.assertTrue(result['findmnt']['filesystems'])

    def test_national_tile_scenario_uses_input_volume_and_keeps_other_denominators_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/'output';out.mkdir();(root/'pins').mkdir()
            fixtures={root/'pins'/'sources.json':{'national_input_volume':{'bytes':10000}},
                      root/'pins'/'extracts.json':[{'bytes':2000}],
                      out/'regional-input.json':{'merged_pbf_bytes':1000},
                      out/'tile-build.json':{'tile_files':5,'tile_bytes':10}}
            for path,value in fixtures.items(): path.write_text(json.dumps(value))
            report={'status':'complete','stages':[{'name':'build_tiles','status':'complete',
                    'elapsed_seconds':100,'peak_working_disk_bytes':pilot.GIB}],
                    'elapsed_seconds':110,'peak_cgroup_memory_bytes':pilot.GIB,
                    'peak_working_disk_bytes':pilot.GIB,'minimum_free_disk_bytes':73*pilot.GIB,
                    'initial_workspace_space':{'available_bytes':80*pilot.GIB}}
            with patch.multiple(pilot,ROOT=root,OUT=out): text=pilot.metric_table(report)
            self.assertIn('input-volume-only hypothesis x10.000',text)
            self.assertIn('RAM is a bounded working set',text)
            self.assertIn('national course-by-candidate-cell/encoded-pair denominator unmeasured',text)



if __name__ == '__main__': unittest.main()
