"""Capacity metrics accept only bounded labels and never alter operations."""

from types import SimpleNamespace

import pytest

from app.core.metrics import registry
from app.modules.storage import capacity_observability
from app.modules.storage.capacity_observability import (
    change_reservations,
    observe_inventory,
    operation_kind,
    record_cleanup,
    record_decision,
    record_prediction_error,
)


class TestCapacityObservability:
    def test_operation_metric_never_uses_private_identifiers(self) -> None:
        assert operation_kind("artifact-upload:private-session") == "artifact-upload"
        assert operation_kind("private-user-value:secret") == "unknown"
        assert operation_kind("../../vault/key") == "unknown"

    def test_forecast_error_observation_has_no_private_labels(self) -> None:
        metric = "printstash_storage_forecast_prediction_error_ratio_count"
        before = registry.get_sample_value(metric) or 0

        record_prediction_error(0.25)

        assert registry.get_sample_value(metric) == before + 1

    def test_ignores_negative_prediction_error(self) -> None:
        metric = "printstash_storage_forecast_prediction_error_ratio_count"
        before = registry.get_sample_value(metric) or 0

        record_prediction_error(-0.25)

        assert (registry.get_sample_value(metric) or 0) == before

    @pytest.mark.parametrize(
        ("metric_name", "record"),
        [
            (
                "capacity_decisions",
                lambda: record_decision(
                    "artifact:private", "allow", "capacity_available"
                ),
            ),
            (
                "active_capacity_reservations",
                lambda: change_reservations("artifact:private", 1),
            ),
            (
                "inventory_bytes",
                lambda: observe_inventory(
                    SimpleNamespace(
                        buckets=[
                            SimpleNamespace(
                                category="stl", lifecycle="live", logical_bytes=1
                            )
                        ],
                        provider_capacity=SimpleNamespace(available_bytes=None),
                    ),
                    provider="local",
                ),
            ),
            ("cleanup_outcomes", lambda: record_cleanup("staging", "success")),
            ("forecast_prediction_error", lambda: record_prediction_error(0.25)),
        ],
    )
    def test_metric_failure_never_alters_the_operation(
        self, monkeypatch, metric_name, record
    ) -> None:
        metric = getattr(capacity_observability, metric_name)
        failing_method = (
            "observe" if metric_name == "forecast_prediction_error" else "labels"
        )
        monkeypatch.setattr(
            metric,
            failing_method,
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("metrics down")),
        )

        record()
