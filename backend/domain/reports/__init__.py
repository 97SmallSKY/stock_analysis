"""backend.domain.reports — W10-2 周报聚合."""
from .weekly import WeeklyReport, build_weekly_report

__all__ = ["WeeklyReport", "build_weekly_report"]
