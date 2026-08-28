from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("bronze/", views.bronze_dashboard, name="bronze-dashboard"),
    path("silver/", views.silver_dashboard, name="silver-dashboard"),
    path("api/dashboard/", views.dashboard_api, name="dashboard-api"),
]
