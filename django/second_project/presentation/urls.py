from django.urls import path

from . import views


app_name = "second_project"

urlpatterns = [
    path("review/", views.review_request, name="review"),
]
