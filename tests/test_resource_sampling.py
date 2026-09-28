import unittest

from evaluation.resource_sampling import summarize_resource_samples


class TestResourceSampling(unittest.TestCase):
    def test_summary_uses_measured_values_and_leaves_missing_metrics_null(self):
        summary = summarize_resource_samples(
            [
                {
                    "cpu_utilization_percent": 40,
                    "ram_used_mb": 1000,
                    "gpu_name": "NVIDIA Test GPU",
                    "gpu_utilization_percent": 60,
                    "vram_used_mb": 4000,
                },
                {
                    "cpu_utilization_percent": 60,
                    "ram_used_mb": 1200,
                    "gpu_name": "NVIDIA Test GPU",
                    "gpu_utilization_percent": 80,
                    "vram_used_mb": 5000,
                },
            ]
        )
        self.assertEqual(summary["cpu_utilization_percent"], 50)
        self.assertEqual(summary["peak_ram_mb"], 1200)
        self.assertEqual(summary["gpu_name"], "NVIDIA Test GPU")
        self.assertEqual(summary["gpu_utilization_percent"], 70)
        self.assertEqual(summary["peak_vram_mb"], 5000)

        empty = summarize_resource_samples([{}])
        self.assertIsNone(empty["cpu_utilization_percent"])
        self.assertIsNone(empty["peak_ram_mb"])
        self.assertIsNone(empty["gpu_utilization_percent"])


if __name__ == "__main__":
    unittest.main()
