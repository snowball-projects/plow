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
    def test_disk_sample_continues_if_a_temporary_file_disappears(self):
        root=Mock();gone=Mock();present=Mock()
        root.rglob.return_value=[gone,present]
        gone.is_file.return_value=True;gone.stat.side_effect=FileNotFoundError
        present.is_file.return_value=True;present.stat.return_value.st_blocks=3
        with patch.object(pilot,'ROOT',root):
            self.assertEqual(pilot.disk_bytes(),3*512)

    def exercise(self, free, allocated, charged_memory=0, process_code=0, existing_oom=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, out, cg = (root / name for name in ['work', 'output', 'cg'])
            for path in [work, out, cg]: path.mkdir()
            for name in ['memory.current', 'memory.peak']: (cg / name).write_text(str(charged_memory))
            (cg / 'memory.stat').write_text(f'anon {pilot.GIB}\nfile {max(0,charged_memory-pilot.GIB)}\n')
            (cg / 'memory.events').write_text(f'oom_kill {existing_oom}\n')
            process = Mock(pid=123)
            process.poll.return_value = process_code
            space = {'available_bytes':free, 'free_bytes':free,
                     'total_bytes':100*pilot.GIB, 'available_inodes':1_000_000,
                     'device_id':'8:1'}
            report = {'stages':[], 'peak_cgroup_memory_bytes':0, 'peak_observed_anon_bytes':0,
                      'peak_working_disk_bytes':0, 'minimum_free_disk_bytes':free,
                      'peak_filesystem_used_bytes':0}
            with patch.multiple(pilot, ROOT=root, WORK=work, OUT=out), \
                 patch.object(pilot, 'workspace_space', return_value=space), \
                 patch.object(pilot, 'disk_bytes', return_value=allocated), \
                 patch.object(pilot.subprocess, 'Popen', return_value=process):
                pilot.run_stage('fixture', report, cg, pilot.time.monotonic())
            return report

    def test_old_oom_event_does_not_reclassify_a_later_tool_error(self):
        with self.assertRaisesRegex(RuntimeError,'exit 2'):
            self.exercise(73*pilot.GIB,pilot.GIB,process_code=2,existing_oom=1)

    def test_working_set_above_old_cap_continues_with_actual_free_space(self):
        report = self.exercise(free=73*pilot.GIB, allocated=20*pilot.GIB)
        self.assertEqual(report['stages'][0]['status'], 'complete')
        self.assertEqual(report['peak_working_disk_bytes'], 20*pilot.GIB)
        self.assertEqual(report['minimum_free_disk_bytes'], 73*pilot.GIB)

    def test_cached_memory_does_not_trigger_an_early_stop_below_enforced_limit(self):
        report=self.exercise(free=73*pilot.GIB,allocated=20*pilot.GIB,
                             charged_memory=13*pilot.GIB-4096)
        self.assertEqual(report['stages'][0]['status'],'complete')
        self.assertEqual(report['peak_observed_anon_bytes'],pilot.GIB)
        self.assertGreater(report['last_memory_stat']['file'],11*pilot.GIB)

    def test_memory_bounds_must_be_enforced_without_swap(self):
        with tempfile.TemporaryDirectory() as directory:
            cg=Path(directory)
            (cg/'memory.max').write_text(str(13*pilot.GIB))
            (cg/'memory.swap.max').write_text('0')
            self.assertEqual(pilot.verify_memory_bounds(cg)['memory_max_bytes'],13*pilot.GIB)
            (cg/'memory.max').write_text('max')
            with self.assertRaisesRegex(RuntimeError,'requires enforced cgroup'):
                pilot.verify_memory_bounds(cg)

    def test_small_working_set_stops_when_filesystem_needs_reserve(self):
        with self.assertRaisesRegex(pilot.CapacityStop, 'system/packaging reserve'):
            self.exercise(free=9*pilot.GIB, allocated=pilot.GIB)

    def test_filesystem_evidence_has_device_and_available_space(self):
        result = pilot.filesystem_evidence()
        self.assertGreater(result['space']['available_bytes'], 0)
        self.assertIn('device_id', result['space'])
        self.assertIn('Filesystem', result['df_B1'])
        self.assertTrue(result['findmnt']['filesystems'])



if __name__=='__main__': unittest.main()
