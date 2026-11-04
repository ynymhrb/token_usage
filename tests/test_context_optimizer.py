import tempfile
import unittest
from pathlib import Path

from codex_token.context_optimizer import ContextOptimizer


class ContextOptimizerTests(unittest.TestCase):
    def test_small_result_is_visible_without_compression(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = ContextOptimizer(Path(temporary), max_chars=80).optimize("ok\n", source="command")
        self.assertEqual(result.visible_text, "ok\n")
        self.assertFalse(result.compressed)
        self.assertFalse(result.duplicate)

    def test_large_result_is_capped_and_full_text_is_local_only(self):
        secret = "secret-value"
        with tempfile.TemporaryDirectory() as temporary:
            result = ContextOptimizer(Path(temporary), max_chars=120).optimize("start\n" + secret * 30 + "\nend\n", source="command")
            saved = Path(temporary, result.storage_name).read_text(encoding="utf-8")
        self.assertTrue(result.compressed)
        self.assertLessEqual(len(result.visible_text), 120)
        self.assertNotIn(secret, result.visible_text)
        self.assertIn(secret, saved)

    def test_failed_output_retains_error_line_within_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            text = "noise\n" * 30 + "ERROR: missing configuration\n" + "noise\n" * 30
            result = ContextOptimizer(Path(temporary), max_chars=100).optimize(text, source="command", failed=True)
        self.assertIn("ERROR: missing configuration", result.visible_text)
        self.assertLessEqual(len(result.visible_text), 100)

    def test_duplicate_result_returns_reference_without_repeating_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            optimizer = ContextOptimizer(Path(temporary), max_chars=100)
            optimizer.optimize("same output", source="command")
            result = optimizer.optimize("same output", source="command")
        self.assertTrue(result.duplicate)
        self.assertNotIn("same output", result.visible_text)
        self.assertIn("重复结果", result.visible_text)

    def test_duplicate_result_survives_optimizer_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            ContextOptimizer(Path(temporary), max_chars=100).optimize("same output", source="command")
            result = ContextOptimizer(Path(temporary), max_chars=100).optimize("same output", source="command")
        self.assertTrue(result.duplicate)
        self.assertNotIn("same output", result.visible_text)

    def test_short_secret_and_error_secret_are_redacted_from_model_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            optimizer = ContextOptimizer(Path(temporary), max_chars=100)
            short = optimizer.optimize("API_KEY=short-secret", source="command")
            failed = optimizer.optimize("noise\nERROR API_KEY=error-secret\n" * 20, source="command", failed=True)
        self.assertNotIn("short-secret", short.visible_text)
        self.assertNotIn("error-secret", failed.visible_text)
        self.assertIn("[凭据已脱敏]", short.visible_text)

    def test_receipt_records_decision_without_raw_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            optimizer = ContextOptimizer(Path(temporary), max_chars=80)
            optimizer.optimize("API_KEY=secret", source="command")
            receipt = next(Path(temporary).glob("*.json")).read_text(encoding="utf-8")
        self.assertIn('"source": "command"', receipt)
        self.assertNotIn("secret", receipt)


if __name__ == "__main__":
    unittest.main()
