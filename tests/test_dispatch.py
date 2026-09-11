import base64
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import dispatch_workflow as dispatcher


class DispatcherTests(unittest.TestCase):
    def invoke(self, status, minutes):
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        content = base64.b64encode(f'timestamp,games\n{stamp},100\n'.encode()).decode()
        with patch.object(dispatcher, 'credential', return_value='test-token'), \
             patch.object(dispatcher, 'request', side_effect=[
                 {'workflow_runs': [{'status': status}]}, {'content': content}, None]) as request:
            result = dispatcher.main()
            return result, request.call_args_list

    def test_active_workflow_is_not_duplicated(self):
        result, calls = self.invoke('in_progress', 60)
        self.assertIn('already active', result)
        self.assertEqual(len(calls), 1)

    def test_recent_snapshot_is_not_recollected(self):
        result, calls = self.invoke('completed', 5)
        self.assertIn('less than 12 minutes', result)
        self.assertEqual(len(calls), 2)

    def test_due_snapshot_dispatches_default_branch(self):
        result, calls = self.invoke('completed', 20)
        self.assertIn('Dispatched', result)
        self.assertEqual(calls[-1].args, ('/actions/workflows/track.yml/dispatches', 'test-token', {'ref': 'main'}))
