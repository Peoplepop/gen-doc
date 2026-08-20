from django.urls import path

from . import views

urlpatterns = [
    path(
        "<int:pk>/feature-selection/",
        views.project_feature_selection,
        name="project-feature-selection",
    ),
]
