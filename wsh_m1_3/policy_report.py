"""Canonical report API; CLI fixture is maintained in generate_report.py."""
from .policy_engine import evaluate_dataset, build_report, write_report

__all__ = ["evaluate_dataset", "build_report", "write_report"]
