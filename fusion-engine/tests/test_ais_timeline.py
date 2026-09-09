"""AIS timeline levels: persistence, averaging, and missing-data semantics."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fusion.backfill import Backfill, STEP
from fusion.ingest_ais import AisFeed
from fusion.pipeline import FusionState
from fusion.store import InMemoryStore
from tests.test_ais import report


class AisTimelineTests(unittest.TestCase):
    def test_availability_is_not_a_zero_count(self):
        feed = AisFeed()
        self.assertIsNone(feed.timeline_count())
        feed.configure([dict(lat=0, lon=0, radius_nm=60)])
        self.assertIsNone(feed.timeline_count())
        feed.state = 'ready'
        self.assertEqual(feed.timeline_count(), 0)
        feed._handle(report())
        self.assertEqual(feed.timeline_count(), 1)
        feed.state = 'error'
        self.assertIsNone(feed.timeline_count())
        feed.configure([])
        feed.state = 'ready'
        self.assertIsNone(feed.timeline_count())

    def test_average_only_available_samples_and_preserve_old_history_gaps(self):
        now = datetime.now(timezone.utc).timestamp()
        t = int(now // STEP) * STEP
        with tempfile.TemporaryDirectory() as directory:
            backfill = Backfill(Path(directory))
            history = [{'t': t, 'ais': 2}, {'t': t, 'ais': 3}, {'t': t, 'ais': None}, {'t': t},
                       {'t': t - STEP, 'tracks': 10}, {'t': t - 2*STEP, 'ais': 0}]
            bins = {b['t']: b for b in backfill.bins(1, history, [])}
            self.assertEqual(bins[t]['ais'], 2.5)
            self.assertIsNone(bins[t-STEP]['ais'])
            self.assertEqual(bins[t-2*STEP]['ais'], 0)
            self.assertIsNone(bins[t-3*STEP]['ais'])

    def test_fusion_persists_ais_and_timeline_reads_it(self):
        with tempfile.TemporaryDirectory() as directory:
            history_file = Path(directory) / 'history.jsonl'
            state = FusionState(store=InMemoryStore(), history=[], regions=[],
                                backfill=Backfill(Path(directory)), ais_count=lambda: 4)
            with patch('fusion.pipeline.HISTORY_FILE', history_file):
                state.fuse()
            persisted = json.loads(history_file.read_text().splitlines()[-1])
            self.assertEqual(persisted['ais'], 4)
            # Simulate loading persisted history after restart.
            state.history = [persisted]
            self.assertEqual(state.api_timeline(1)['bins'][-1]['ais'], 4)

if __name__ == '__main__':
    unittest.main()
