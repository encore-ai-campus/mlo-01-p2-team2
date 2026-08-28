from __future__ import annotations

import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from second_project.repository.dashboard import DashboardConfig, DashboardRepository


logger = logging.getLogger(__name__)

MONGODB_DASHBOARD_ERROR = (
    "MongoDB에 연결하거나 대시보드 컬렉션을 조회하지 못했습니다. "
    "MongoDB 실행 상태와 DASHBOARD_* 설정을 확인하세요."
)


def dashboard(request):  # noqa: ARG001
    """Render a read-only summary of the validation MongoDB collections."""

    config = DashboardConfig.from_settings()
    context = {
        "config": config.as_dict(),
        "error": None,
        "summary": {
            "success_count": 0,
            "failure_count": 0,
            "restoration": {
                "evaluated": False,
                "total": 0,
                "success_rate": None,
                "failure_rate": None,
                "success_rate_display": "-",
                "failure_rate_display": "-",
            },
            "run_count": 0,
            "bronze_count": 0,
            "silver_total": 0,
        },
        "silver_models": [],
        "error_breakdown": [],
        "recent_runs": [],
        "recent_failures": [],
        "latest_run": None,
        "version": {
            "token": "none",
            "run_id": None,
            "status": "NO_DATA",
            "finished_at": None,
        },
    }
    try:
        context.update(DashboardRepository(config).snapshot())
    except Exception:  # pragma: no cover - exercised by a live MongoDB outage
        logger.exception("MongoDB dashboard query failed")
        context["error"] = MONGODB_DASHBOARD_ERROR
    return render(request, "second_project/dashboard.html", context)


@require_GET
def dashboard_api(request):
    """Return a dashboard snapshot after the latest completed run changes."""

    config = DashboardConfig.from_settings()
    try:
        repository = DashboardRepository(config)
        version = repository.latest_version()
        if request.GET.get("since") == version["token"]:
            return JsonResponse(
                {
                    "ok": True,
                    "changed": False,
                    "version": version,
                }
            )

        snapshot = repository.snapshot()
        return JsonResponse(
            {
                "ok": True,
                "changed": True,
                "version": snapshot["version"],
                "snapshot": snapshot,
            }
        )
    except Exception:  # pragma: no cover - exercised by a live MongoDB outage
        logger.exception("MongoDB dashboard API query failed")
        return JsonResponse(
            {
                "ok": False,
                "error": MONGODB_DASHBOARD_ERROR,
            },
            status=503,
        )
