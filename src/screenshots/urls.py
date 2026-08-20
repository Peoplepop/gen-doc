from django.urls import path

from . import views

urlpatterns = [
    path(
        "feature-nodes/<int:node_pk>/screenshot-requirements/",
        views.screenshot_requirement_list,
        name="screenshot-requirement-list",
    ),
    path(
        "screenshot-requirements/<int:pk>/",
        views.screenshot_requirement_detail,
        name="screenshot-requirement-detail",
    ),
    path(
        "projects/<int:project_pk>/screenshots/",
        views.project_screenshot_checklist,
        name="project-screenshot-checklist",
    ),
    path(
        "projects/<int:project_pk>/screenshots/<int:requirement_pk>/default/",
        views.project_screenshot_default,
        name="project-screenshot-default",
    ),
    path(
        "projects/<int:project_pk>/screenshots/<int:requirement_pk>/custom/",
        views.project_screenshot_custom_list,
        name="project-screenshot-custom-list",
    ),
    path(
        "projects/<int:project_pk>/screenshots/<int:requirement_pk>/custom/<int:custom_pk>/",
        views.project_screenshot_custom_detail,
        name="project-screenshot-custom-detail",
    ),
    path(
        "projects/<int:project_pk>/screenshots/<int:requirement_pk>/resolve/",
        views.project_screenshot_resolve,
        name="project-screenshot-resolve",
    ),
]
