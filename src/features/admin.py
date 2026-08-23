from django.contrib import admin

from .models import FeatureNode, FeatureNodeContent


@admin.register(FeatureNode)
class FeatureNodeAdmin(admin.ModelAdmin):
    """節點刪除為真實刪除（無 soft-delete）：後台的「刪除」按鈕沿用
    Django admin 預設行為（呼叫 model 的 .delete()）。`parent` 為
    on_delete=PROTECT，仍有子孫的節點無法直接刪除，需先處理子孫節點。"""


admin.site.register(FeatureNodeContent)
