"""A/B testing as an isolated module (spec/ab-testing.md).

Everything the module owns lives here, in ``web/routes/experiments.py`` and in the「A/B 测试」tab. Existing code may
reference it only from the allowlist in spec §2 (rule 1); ``tests/test_experiments.py`` scans for that.
"""
