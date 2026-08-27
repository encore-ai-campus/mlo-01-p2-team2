from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.http.request import split_domain_port


class LocalNullOriginMiddleware:
    """Support sandboxed local preview browsers without weakening production CSRF."""

    loopback_hosts = {"127.0.0.1", "localhost", "[::1]"}

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if settings.DEBUG and request.META.get("HTTP_ORIGIN") == "null":
            domain, _ = split_domain_port(request.get_host())
            if domain in self.loopback_hosts:
                request.META.pop("HTTP_ORIGIN", None)
        return self.get_response(request)
