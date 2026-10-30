"""Transient local HTTP failures must not become unreachable routes."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from osrm import Client,QueryError
import overnight


class Transport(unittest.TestCase):
    def test_disconnected_get_is_retried_and_keeps_response(self):
        session=Mock();response=Mock();response.json.return_value={'code':'Ok','durations':[[123]]}
        session.get.side_effect=[requests.ConnectionError('closed'),response]
        with patch('osrm.time.sleep'):
            self.assertEqual(Client(session).table([{'point':(1,2)}],[{'point':(3,4)}]),[[123]])
        self.assertEqual(session.get.call_count,2)

    def test_exhausted_transport_is_an_error_not_a_failed_snap(self):
        session=Mock();session.get.side_effect=requests.Timeout('offline')
        with patch('osrm.time.sleep'),self.assertRaises(requests.Timeout):Client(session).nearest((1,2))
        self.assertEqual(session.get.call_count,3)

    def test_osrm_query_error_is_not_transport_retried(self):
        session=Mock();session.get.return_value.json.return_value={'code':'TooBig'}
        with self.assertRaises(QueryError):Client(session).table([{'point':(1,2)}],[{'point':(3,4)}])
        self.assertEqual(session.get.call_count,1)

    def test_focused_retry_omits_only_texas_build(self):
        report={'states':{},'variants':[],'stages':[]}
        experiment=overnight.Experiments(report,None,0)
        def build(state):report['states'][state]={'network_complete':True,'mode':False}
        with patch.dict('os.environ',{'PLOW_INCLUDE_TEXAS':'false'}), \
             patch.object(experiment,'build',side_effect=build) as builds, \
             patch.object(experiment,'evaluate') as evaluate,patch.object(experiment,'swap'), \
             patch('overnight.pilot.stop_containers'),patch('overnight.shutil.rmtree'), \
             patch('overnight.attach_resources'),patch('overnight.national_estimate'):
            experiment.run()
        builds.assert_called_once_with('illinois')
        self.assertEqual(evaluate.call_count,11)

    def test_stock500_recovery_scores_only_the_three_missing_cases(self):
        report={'states':{},'variants':[],'stages':[]}
        experiment=overnight.Experiments(report,None,0)
        def build(state):report['states'][state]={'network_complete':True,'mode':False}
        with patch.dict('os.environ',{'PLOW_STOCK500_ONLY':'true','PLOW_INCLUDE_TEXAS':'true'}), \
             patch.object(experiment,'build',side_effect=build) as builds, \
             patch.object(experiment,'evaluate') as evaluate,patch.object(experiment,'swap'), \
             patch('overnight.pilot.stop_containers'),patch('overnight.shutil.rmtree'), \
             patch('overnight.attach_resources'),patch('overnight.national_estimate'):
            experiment.run()
        builds.assert_called_once_with('illinois')
        self.assertEqual([tuple(c.args) for c in evaluate.call_args_list],[(500,600),(500,900),(500,1200)])
        self.assertEqual(evaluate.call_args_list[0].kwargs,{'new_table':True})


if __name__=='__main__':unittest.main()
