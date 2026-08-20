from django.urls import path

from . import views

# 掛在 'api/projects/' 前綴下（見 gen_doc/urls.py）——跟 selections.urls
# 共用同一個前綴、各自負責自己的路徑片段，是既有慣例。
urlpatterns = [
    path(
        "<int:pk>/content-overrides/",
        views.project_content_override_list,
        name="project-content-override-list",
    ),
    path(
        "<int:pk>/content-overrides/<int:node_id>/<str:document_type>/",
        views.project_content_override_detail,
        name="project-content-override-detail",
    ),
    path(
        "<int:pk>/custom-sections/",
        views.project_custom_section_list,
        name="project-custom-section-list",
    ),
    path(
        "<int:pk>/custom-sections/<int:section_id>/",
        views.project_custom_section_detail,
        name="project-custom-section-detail",
    ),
]
