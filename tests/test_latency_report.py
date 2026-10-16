import unittest
from latency_report import summarize


class LatencyReportTests(unittest.TestCase):
    def test_sdk_seconds_and_server_milliseconds_are_not_mixed(self):
        report = summarize({'events': [
            {'type':'sdk_metrics','message_id':'one','metrics':{'e2e_latency':.2}},
            {'type':'sdk_metrics','message_id':'two','metrics':{'e2e_latency':.6}},
            {'type':'latency','server_response_ms':100},
            {'type':'latency','server_response_ms':300},
        ]})
        self.assertEqual(report['sdk_e2e_ms'],{'samples':2,'p50_ms':400,'p95_ms':580})
        self.assertEqual(report['server_response_ms'],{'samples':2,'p50_ms':200,'p95_ms':290})

    def test_missing_and_invalid_values_are_not_reported_as_zero(self):
        report = summarize({'events': [
            {'type':'sdk_metrics','metrics':{'llm_node_ttft':.2}},
            {'type':'latency','server_response_ms':float('nan'),'server_interruption_ms':True},
        ]})
        self.assertTrue(all(value == {'samples':0,'p50_ms':None,'p95_ms':None} for value in report.values()))

    def test_repeated_sdk_message_is_counted_once(self):
        event = {'type':'sdk_metrics','session_id':'same','message_id':'same','metrics':{'e2e_latency':.4}}
        self.assertEqual(summarize({'events':[event,event]})['sdk_e2e_ms']['samples'],1)

    def test_overlap_is_preserved_instead_of_discarded(self):
        event = {'type':'sdk_metrics','metrics':{'e2e_latency':-.02}}
        self.assertEqual(summarize({'events':[event]})['sdk_e2e_ms']['p50_ms'],-20)
