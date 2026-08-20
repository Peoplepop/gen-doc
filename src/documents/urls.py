from django.urls import path

from . import views

# 掛在 'api/projects/' 前綴下（見 gen_doc/urls.py）——跟 selections.urls／
# overrides.urls／assembly.urls 共用同一個前綴、各自負責自己的路徑片段，
# 是既有慣例。`documents-status/` 刻意用固定字面路徑（不是 `<int:pk>/...`
# 底下的子路徑）——它是「跨所有專案」的彙總端點，語意上不屬於單一
# `<int:pk>` 底下，跟 `<int:pk>/documents/...` 系列端點分開。
urlpatterns = [
    path(
        "<int:pk>/documents/<str:document_type>/generate/",
        views.generate_document,
        name="document-generate",
    ),
    path(
        "<int:pk>/documents/<str:document_type>/history/",
        views.document_history,
        name="document-history",
    ),
    path(
        "documents-status/",
        views.project_document_status_list,
        name="document-status-list",
    ),
]
