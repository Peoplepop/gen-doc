from django.contrib import admin

from .models import ProjectScreenshot, ScreenshotRequirement

admin.site.register(ScreenshotRequirement)
admin.site.register(ProjectScreenshot)
