"""End-to-end integration tests (issue #63).

These tests exercise full workflows across multiple VSA slices — not
individual handler unit tests. They run under the ``integration`` pytest
marker and are excluded from the default ``pytest`` run for fast unit
feedback.

Run explicitly::

    uv run --frozen pytest tests/integration/ -m integration -v
"""
