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


def _render_dashboard(
    request,
    *,
    template_name: str,
    page_key: str,
    page_title: str,
    page_description: str,
):
    """Render one read-only dashboard layer using the shared snapshot."""

    config = DashboardConfig.from_settings()
    context = {
        "page_key": page_key,
        "page_title": page_title,
        "page_description": page_description,
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
    return render(request, template_name, context)


def dashboard(request):  # noqa: ARG001
    """Render the whole validation pipeline overview."""

    return _render_dashboard(
        request,
        template_name="second_project/overview.html",
        page_key="overview",
        page_title="Validation Pipeline",
        page_description="Bronze 원문과 Silver 표준화·검증 결과의 전체 요약",
    )


def bronze_dashboard(request):  # noqa: ARG001
    """Render the Bronze collection and pipeline-operation page."""

    return _render_dashboard(
        request,
        template_name="second_project/bronze.html",
        page_key="bronze",
        page_title="Bronze Layer",
        page_description="수집 원문·Bronze 적재·파이프라인 실행 상태",
    )


def silver_dashboard(request):  # noqa: ARG001
    """Render the Silver standardization and quality page."""

    return _render_dashboard(
        request,
        template_name="second_project/silver.html",
        page_key="silver",
        page_title="Silver Layer",
        page_description="표준화 성공·실패·최종 검증과 Silver 모델 결과",
    )


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
