"""
Факторы формирования СПП: реестр, источники данных и балльная оценка.

Публичный вход в подсистему:

    from app.core.factors import FactorScorer, get_registry, get_inputs

Подробности модели — в docs/FACTORS_SPP.md.
"""
from app.core.factors.inputs import FactorInputs, get_inputs, load_inputs
from app.core.factors.registry import (
    Band,
    Factor,
    FactorRegistry,
    get_registry,
    load_registry,
)
from app.core.factors.scoring import (
    NEUTRAL_SCORE,
    FactorScorer,
    FactorValue,
    FieldFacts,
    ScoreBreakdown,
    blocked_cultures,
    build_field_facts,
    coverage_report,
)

__all__ = [
    "Band",
    "Factor",
    "FactorInputs",
    "FactorRegistry",
    "FactorScorer",
    "FactorValue",
    "FieldFacts",
    "NEUTRAL_SCORE",
    "ScoreBreakdown",
    "blocked_cultures",
    "build_field_facts",
    "coverage_report",
    "get_inputs",
    "get_registry",
    "load_inputs",
    "load_registry",
]
