from django.contrib import admin

from .models import ProjectCustomSection, ProjectFeatureContentOverride

admin.site.register(ProjectFeatureContentOverride)
admin.site.register(ProjectCustomSection)
