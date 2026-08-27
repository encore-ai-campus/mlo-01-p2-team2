from __future__ import annotations

import logging

from django.shortcuts import render

from second_project.repository.dashboard import DashboardConfig, DashboardRepository


logger = logging.getLogger(__name__)


def dashboard(request):  # noqa: ARG001
    """Render a read-only summary of the validation MongoDB collections."""

    config = DashboardConfig.from_settings()
    context = {
        "config": config.as_dict(),
        "error": None,
        "summary": {
            "success_count": 0,
            "failure_count": 0,
            "run_count": 0,
            "bronze_count": 0,
            "silver_total": 0,
        },
        "silver_models": [],
        "error_breakdown": [],
        "recent_runs": [],
        "recent_failures": [],
    }
    try:
        context.update(DashboardRepository(config).snapshot())
    except Exception:  # pragma: no cover - exercised by a live MongoDB outage
        logger.exception("MongoDB dashboard query failed")
        context["error"] = (
            "MongoDB에 연결하거나 대시보드 컬렉션을 조회하지 못했습니다. "
            "MongoDB 실행 상태와 DASHBOARD_* 설정을 확인하세요."
        )
    return render(request, "second_project/dashboard.html", context)
