import unittest

from douyin_creator_mcp.services.metrics import compute_derived_metrics, percentile_rank


class MetricsTests(unittest.TestCase):
    def test_derived_metrics_use_one_snapshot_and_keep_missing_as_none(self) -> None:
        metrics = compute_derived_metrics(
            {
                "exposure_count": 200,
                "play_count": 100,
                "like_count": 10,
                "collect_count": None,
                "comment_count": 2,
                "share_count": 1,
            }
        )

        self.assertEqual(metrics["like_rate"], 0.1)
        self.assertEqual(metrics["play_rate"], 0.5)
        self.assertIsNone(metrics["collect_rate"])
        self.assertIsNone(metrics["interaction_rate"])

    def test_zero_denominator_never_fabricates_rate(self) -> None:
        metrics = compute_derived_metrics({"play_count": 0, "like_count": 2})
        self.assertIsNone(metrics["like_rate"])

    def test_percentile_rank_handles_ties(self) -> None:
        self.assertEqual(percentile_rank(2, [1, 2, 2, 3]), 50.0)


if __name__ == "__main__":
    unittest.main()
