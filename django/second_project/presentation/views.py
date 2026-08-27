from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from second_project.services.continuity_assessment import (
    AssessmentNotAvailable,
    assess_manager_continuity,
    guidance_for,
    summarize_assessment,
)

from .forms import ManagerReviewForm
from .permissions import can_review, is_hr_reviewer


@login_required
@never_cache
def review_request(request: HttpRequest) -> HttpResponse:
    _require_review_permission(request)
    form = ManagerReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        return _render_result(request, form.cleaned_data["manager_id"])
    return _private_response(render(
        request,
        "second_project/review_form.html",
        {"form": form, "is_hr": is_hr_reviewer(request.user)},
    ))


def _render_result(request: HttpRequest, manager_id: str) -> HttpResponse:
    try:
        assessment = assess_manager_continuity(manager_id)
    except AssessmentNotAvailable as error:
        if is_hr_reviewer(request.user):
            error_message = str(error)
            error_code = error.code
            status = 404 if error.code == "TARGET_NOT_FOUND" else 400
        else:
            error_message = "승인된 요청 대상과 현재 등록정보를 확인해 주세요."
            error_code = "REQUEST_NOT_AVAILABLE"
            status = 400
        return _private_response(render(
            request,
            "second_project/review_error.html",
            {"error_message": error_message, "error_code": error_code},
            status=status,
        ))

    if is_hr_reviewer(request.user):
        return _private_response(render(
            request,
            "second_project/hr_result.html",
            {
                "assessment": assessment,
                "guidance": guidance_for(assessment.status),
                "is_hr": True,
            },
        ))
    summary = summarize_assessment(assessment)
    return _private_response(render(
        request,
        "second_project/team_result.html",
        {
            "assessment": summary,
            "guidance": guidance_for(summary.status),
            "is_hr": False,
        },
    ))


def _require_review_permission(request: HttpRequest) -> None:
    if not can_review(request.user):
        raise PermissionDenied("인사 요청 검토 권한이 없습니다.")


def _private_response(response: HttpResponse) -> HttpResponse:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
