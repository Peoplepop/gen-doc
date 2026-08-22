from django.contrib import admin

from .models import FeatureNode, FeatureNodeContent


@admin.register(FeatureNode)
class FeatureNodeAdmin(admin.ModelAdmin):
    """後台的「刪除」按鈕預設會真的從資料庫移除節點，跟這個系統的
    軟刪除／停用政策（`is_enabled=False`，資料與既有專案關聯永遠不斷裂）
    不一致——見 `projects/admin.py::ProjectAdmin` 的同一個修正與理由。"""

    def delete_model(self, request, obj):
        obj.is_enabled = False
        obj.save(update_fields=["is_enabled", "updated_at"])

    def delete_queryset(self, request, queryset):
        queryset.update(is_enabled=False)


admin.site.register(FeatureNodeContent)
