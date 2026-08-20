from django.urls import path

from . import views

urlpatterns = [
    path("", views.feature_node_list, name="feature-node-list"),
    path("tree/", views.feature_node_tree, name="feature-node-tree"),
    path("<int:pk>/", views.feature_node_detail, name="feature-node-detail"),
    path(
        "<int:pk>/contents/",
        views.feature_node_content_list,
        name="feature-node-content-list",
    ),
    path(
        "<int:pk>/contents/<str:document_type>/",
        views.feature_node_content_detail,
        name="feature-node-content-detail",
    ),
]
