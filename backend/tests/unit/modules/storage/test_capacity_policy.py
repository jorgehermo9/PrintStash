"""Unknown quota is distinct from exhausted quota; headroom applies once per volume."""

import pytest

from app.modules.storage.capacity_policy import CapacityPolicy


class TestCapacityPolicy:
    @pytest.mark.parametrize(
        "absolute,percent,total,expected",
        [(100, 5, 1000, 100), (10, 5, 1000, 50), (0, 1, 101, 2)],
    )
    def test_reserves_the_larger_headroom(self, absolute, percent, total, expected):
        policy = CapacityPolicy(absolute, percent)
        assert policy.headroom(total) == expected

    def test_allows_exact_remaining_capacity(self):
        decision = CapacityPolicy(10, 10).evaluate(
            50, available_bytes=100, reserved_bytes=40, total_bytes=100
        )
        assert decision.outcome == "allow"

    def test_denies_one_byte_above_admissible_capacity(self):
        decision = CapacityPolicy(10).evaluate(
            51, available_bytes=100, reserved_bytes=40
        )
        assert decision.outcome == "deny"
        assert (
            decision.required_bytes,
            decision.available_bytes,
            decision.reserved_bytes,
            decision.headroom_bytes,
        ) == (51, 100, 40, 10)

    @pytest.mark.parametrize("available,outcome", [(None, "warn"), (0, "deny")])
    def test_distinguishes_unknown_capacity_from_zero(self, available, outcome):
        assert (
            CapacityPolicy().evaluate(1, available_bytes=available).outcome == outcome
        )

    def test_warns_when_percentage_headroom_cannot_be_measured(self):
        decision = CapacityPolicy(10, 5).evaluate(1, available_bytes=100)
        assert (decision.outcome, decision.reason) == (
            "warn",
            "capacity_total_unknown",
        )

    @pytest.mark.parametrize("percent", [-1, 101, float("nan"), float("inf")])
    def test_rejects_invalid_percentage(self, percent):
        with pytest.raises(ValueError):
            CapacityPolicy(0, percent)

    @pytest.mark.parametrize(
        "values",
        [
            {"required_bytes": -1},
            {"reserved_bytes": -1},
            {"total_bytes": -1},
            {"available_bytes": -1},
        ],
    )
    def test_rejects_negative_evidence(self, values):
        args = {"required_bytes": 0, "available_bytes": None, **values}
        with pytest.raises(ValueError):
            CapacityPolicy().evaluate(**args)
