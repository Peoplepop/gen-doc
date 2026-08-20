from django.contrib import admin

from .models import ProjectFeatureExclusion, ProjectFeatureSelection

admin.site.register(ProjectFeatureSelection)
admin.site.register(ProjectFeatureExclusion)
