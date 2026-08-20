"""
URL configuration for gen_doc project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('core.urls')),
    path('api/auth/', include('accounts.urls')),
    path('api/projects/', include('projects.urls')),
    path('api/projects/', include('selections.urls')),
    path('api/projects/', include('overrides.urls')),
    path('api/feature-nodes/', include('features.urls')),
    path('api/feature-nodes/', include('overrides.node_urls')),
    path('api/', include('screenshots.urls')),
    path('api/projects/', include('assembly.urls')),
]

if settings.DEBUG:
    # 本機開發時直接由 Django 服務上傳的截圖檔案；正式部署另有 web
    # server／物件儲存負責，不透過這行。
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
