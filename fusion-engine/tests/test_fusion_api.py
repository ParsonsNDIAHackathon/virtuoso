"""The click-two HTTP workflow must coexist with the live AIS routes."""
import importlib
import unittest
from unittest.mock import Mock, patch

from fusion.fusion_ai import AIProviderError, AIUnavailable
from fusion.ingest_ais import AisFeed
from tests.test_ais_api import Client, State


class FusionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("fusion.pipeline.FusionState", State):
            cls.server = importlib.import_module("app.server")

    def setUp(self):
        self.state = State()
        self.state.adjudicate_pair = Mock(return_value={"verdict": "PLAUSIBLE"})
        self.enterContext(patch.object(self.server, "state", self.state))
        self.enterContext(patch.object(self.server, "ais", AisFeed()))
        self.client = Client(self.server.app)
        self.pair = {
            "left": {"kind": "gdelt", "id": "gdelt:1"},
            "right": {"kind": "adsb", "id": "adsb:abc"},
        }

    def test_live_comparison_and_ais_share_the_api(self):
        response = self.client.post("/api/fusion/adjudicate", json=self.pair)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "PLAUSIBLE")
        self.state.adjudicate_pair.assert_called_once_with(
            "gdelt", "gdelt:1", "adsb", "adsb:abc", force=False,
        )
        response = self.client.get("/api/ais")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["vessels"], [])

    def test_replay_uses_selected_time_and_force(self):
        replay = Mock()
        replay.adjudicate_pair.return_value = {"verdict": "SUPPORTED"}
        with patch.object(self.server, "_replay", return_value=replay) as lookup:
            response = self.client.post("/api/fusion/adjudicate", json={
                **self.pair, "mode": "hormuz-2026-08-18", "t": 1787047200, "force": True,
            })
        self.assertEqual(response.status_code, 200)
        lookup.assert_called_once_with("hormuz-2026-08-18")
        replay.adjudicate_pair.assert_called_once_with(
            1787047200, "gdelt", "gdelt:1", "adsb", "adsb:abc", force=True,
        )
        self.state.adjudicate_pair.assert_not_called()

    def test_invalid_selection_never_calls_the_adjudicator(self):
        with patch.object(self.server, "_replay") as replay:
            for body in (
                {**self.pair, "right": self.pair["left"]},
                {**self.pair, "mode": "hormuz-2026-08-18"},
            ):
                with self.subTest(body=body):
                    response = self.client.post("/api/fusion/adjudicate", json=body)
                    self.assertEqual(response.status_code, 422)
            replay.assert_not_called()
        self.state.adjudicate_pair.assert_not_called()

    def test_provider_and_expired_record_errors_reach_the_ui(self):
        for error, status, detail in (
            (AIUnavailable("OPENAI_API_KEY is not configured"), 503, "OPENAI_API_KEY"),
            (AIProviderError("Quota exhausted", status_code=429, code="insufficient_quota"),
             429, "insufficient_quota"),
            (KeyError("record is no longer in the current picture"), 404, "current picture"),
        ):
            with self.subTest(status=status):
                self.state.adjudicate_pair.side_effect = error
                response = self.client.post("/api/fusion/adjudicate", json=self.pair)
                self.assertEqual(response.status_code, status)
                self.assertIn(detail, response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
