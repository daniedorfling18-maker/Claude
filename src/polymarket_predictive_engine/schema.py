from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BaseRow:
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]):
        fields = getattr(cls, "__dataclass_fields__", {})
        kwargs = {name: data.get(name) for name in fields if name != "raw"}
        kwargs["raw"] = dict(data)
        return cls(**kwargs)


@dataclass
class MarketSnapshot(BaseRow):
    snapshot_timestamp: str | None = None
    market_id: str | None = None
    token_id: str | None = None
    question: str | None = None
    category: str | None = None
    outcome: str | None = None
    bid: float | None = None
    ask: float | None = None
    midpoint: float | None = None
    liquidity: float | None = None
    volume: float | None = None


@dataclass
class JoinedSnapshot(BaseRow):
    snapshot_timestamp: str | None = None
    market_id: str | None = None
    slug: str | None = None
    question: str | None = None
    category: str | None = None


@dataclass
class CollectorHeartbeat(BaseRow):
    category: str | None = None
    heartbeat_timestamp: str | None = None
    status: str | None = None


@dataclass
class CollectorState(BaseRow):
    category: str | None = None
    last_snapshot_timestamp: str | None = None
    market_count: int | None = None


@dataclass
class DockerServiceInventoryRow(BaseRow):
    service_name: str | None = None
    image_name: str | None = None
    command: str | None = None
    script_entry_point: str | None = None
    category_covered: str | None = None
    input_files: str | None = None
    output_files: str | None = None
    polling_frequency: str | None = None
    environment_variables_used: str | None = None
    writes_raw_data: bool = False
    writes_transformed_data: bool = False
    writes_ml_predictions: bool = False
    writes_opportunities: bool = False
    writes_execution_logs: bool = False
    writes_state_files: bool = False
    can_place_orders: bool = False
    monitor_only: bool = True
    paper_only: bool = True
    has_live_trading_path: bool = False
    secrets_required: bool = False
    data_timestamped_point_in_time: bool = False
    suitable_for_model_training: bool = False
    suitable_only_for_diagnostics: bool = False
    known_failure_modes: str | None = None
    duplicate_writer_risk: str | None = None
    conflicting_signal_risk: str | None = None


@dataclass
class PipelineHealthRow(BaseRow):
    category: str | None = None
    last_heartbeat: str | None = None
    collector_freshness_minutes: float | None = None
    raw_snapshots_growing: bool = False
    latest_joined_updating: bool = False
    opportunities_generated: bool = False
    execution_log_non_empty: bool = False
    stalled: bool = False
    placeholder_only_ml: bool = False
    insufficient_data_for_training: bool = True
    resolved_labels_available: bool = False
    duplicate_writer_risk: bool = False
    service_can_execute_orders: bool = False


@dataclass
class MarketResolution(BaseRow):
    market_id: str | None = None
    resolution_time: str | None = None
    winning_outcome: str | None = None
    winning_token_id: str | None = None
    quality_status: str | None = None


@dataclass
class TrainingLabel(BaseRow):
    market_id: str | None = None
    token_id: str | None = None
    prediction_timestamp: str | None = None
    close_time: str | None = None
    resolution_time: str | None = None
    target: int | None = None
    horizon: str | None = None


@dataclass
class FeatureRow(BaseRow):
    market_id: str | None = None
    token_id: str | None = None
    prediction_timestamp: str | None = None
    category: str | None = None
    midpoint: float | None = None
    spread: float | None = None
    liquidity: float | None = None
    time_to_close_hours: float | None = None


@dataclass
class ExternalSignal(BaseRow):
    timestamp: str | None = None
    market_slug: str | None = None
    outcome: str | None = None
    fair_probability: float | None = None
    confidence: float | None = None
    source: str | None = None
    notes: str | None = None


@dataclass
class ModelPrediction(BaseRow):
    market_id: str | None = None
    token_id: str | None = None
    prediction_timestamp: str | None = None
    raw_probability: float | None = None
    calibrated_probability: float | None = None
    market_midpoint: float | None = None
    executable_price: float | None = None
    edge: float | None = None
    confidence: float | None = None
    uncertainty_low: float | None = None
    uncertainty_high: float | None = None
    model_version: str | None = None
    feature_set_version: str | None = None
    training_data_cutoff_timestamp: str | None = None
    git_commit_hash: str | None = None


@dataclass
class CalibrationBucket(BaseRow):
    bucket: str | None = None
    count: int = 0
    mean_prediction: float | None = None
    realized_rate: float | None = None


@dataclass
class BaselineResult(BaseRow):
    name: str | None = None
    brier_score: float | None = None
    log_loss: float | None = None
    roi: float | None = None


@dataclass
class ValidationReport(BaseRow):
    model_version: str | None = None
    approved_for_paper_trading: bool = False
    approved_for_live_trading: bool = False
    brier_score: float | None = None
    log_loss: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class TradeSignal(BaseRow):
    market_id: str | None = None
    market_slug: str | None = None
    question: str | None = None
    category: str | None = None
    outcome: str | None = None
    token_id: str | None = None
    side: str | None = None
    market_price: float | None = None
    executable_price: float | None = None
    model_probability: float | None = None
    calibrated_probability: float | None = None
    edge: float | None = None
    confidence: float | None = None
    approval_reason: str | None = None


@dataclass
class RejectedSignal(BaseRow):
    market_id: str | None = None
    token_id: str | None = None
    side: str | None = None
    rejection_reason: str | None = None


@dataclass
class RiskDecision(BaseRow):
    approved: bool = False
    reason: str | None = None
    size: float = 0.0
    max_size: float = 0.0


@dataclass
class PaperOrder(BaseRow):
    order_id: str | None = None
    market_id: str | None = None
    token_id: str | None = None
    side: str | None = None
    size: float = 0.0
    price: float = 0.0
    status: str = "simulated"


@dataclass
class LiveOrder(PaperOrder):
    approval_file: str | None = None
    request_id: str | None = None


@dataclass
class Fill(BaseRow):
    order_id: str | None = None
    fill_timestamp: str | None = None
    token_id: str | None = None
    size: float = 0.0
    price: float = 0.0


@dataclass
class Position(BaseRow):
    token_id: str | None = None
    market_id: str | None = None
    category: str | None = None
    quantity: float = 0.0
    average_entry_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0


@dataclass
class PortfolioSnapshot(BaseRow):
    timestamp: str | None = None
    cash: float = 0.0
    total_exposure: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    drawdown: float = 0.0
    daily_loss: float = 0.0


@dataclass
class ModelRun(BaseRow):
    run_id: str | None = None
    model_version: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    status: str | None = None
    git_commit_hash: str | None = None


@dataclass
class DataQualityIssue(BaseRow):
    file_path: str | None = None
    category: str | None = None
    market_id: str | None = None
    severity: str = "informational"
    issue_type: str | None = None
    message: str | None = None


@dataclass
class DataQualityReport(BaseRow):
    blocker_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    informational_count: int = 0


@dataclass
class BacktestTrade(BaseRow):
    market_id: str | None = None
    token_id: str | None = None
    entry_timestamp: str | None = None
    exit_timestamp: str | None = None
    side: str | None = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    size: float = 0.0
    pnl: float = 0.0


@dataclass
class BacktestReport(BaseRow):
    trade_count: int = 0
    total_pnl: float = 0.0
    roi: float = 0.0
    max_drawdown: float = 0.0
    approved: bool = False
