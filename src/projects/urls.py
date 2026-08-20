from django.urls import path

from . import views

urlpatterns = [
    path("", views.project_list, name="project-list"),
    path("<int:pk>/", views.project_detail, name="project-detail"),
    path("<int:pk>/duplicate/", views.project_duplicate, name="project-duplicate"),
]
