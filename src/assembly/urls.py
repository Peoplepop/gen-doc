from django.urls import path

from . import views

# 掛在 'api/projects/' 前綴下（見 gen_doc/urls.py）——跟 selections.urls／
# overrides.urls 共用同一個前綴、各自負責自己的路徑片段，是既有慣例。
urlpatterns = [
    path(
        "<int:pk>/preview/<str:document_type>/",
        views.project_preview_detail,
        name="project-preview-detail",
    ),
    path(
        "<int:pk>/preview/<str:document_type>/adjustments/",
        views.project_preview_adjustments,
        name="project-preview-adjustments",
    ),
    path(
        "<int:pk>/preview/<str:document_type>/validate/",
        views.project_preview_validate,
        name="project-preview-validate",
    ),
]
