from django.urls import path

from . import views

# 掛在 'api/feature-nodes/' 前綴下（見 gen_doc/urls.py）——跟 features.urls
# 共用同一個前綴，這裡只提供唯讀的「覆寫可視化清單」端點，不觸碰
# features app 本身的任何檔案。
urlpatterns = [
    path(
        "<int:pk>/overrides/",
        views.feature_node_override_list,
        name="feature-node-override-list",
    ),
    path(
        "<int:pk>/overrides/<str:document_type>/",
        views.feature_node_override_list_by_type,
        name="feature-node-override-list-by-type",
    ),
]
