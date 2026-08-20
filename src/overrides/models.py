from django.db import models

from features.models import DOCUMENT_TYPE_CHOICES, FeatureNode, FeatureNodeContent
from projects.models import Project

# T6 scope (Issue #7): 內容覆寫＋自訂章節＋覆寫可視化清單.
#
# Three-layer content model (見 Issue #1 Core Domain Model):
#   共用預設 (FeatureNodeContent, owned by `features` app)
#     -> 專案可選擇性覆寫 (ProjectFeatureContentOverride, this app)
#       -> 最終產出 (T7's document-assembly job — not built here).
#
# This app deliberately does NOT build the "resolve effective content for
# assembly" engine (that's T7). It does provide `get_effective_content()`
# below and a matching read endpoint so T7 has a ready-made query surface:
# "what would a project+node+document_type resolve to right now".


class ProjectFeatureContentOverride(models.Model):
    """一個專案針對某 FeatureNode 在某文件類型下的內容覆寫。

    只影響「這一個」(project, node, document_type) 組合——同一個節點在
    其他文件類型下、或其他專案，完全不受影響（Issue #7 acceptance
    criterion 1、2）。當沒有覆寫列存在時，該 (project, node,
    document_type) 一律視為使用 `FeatureNodeContent` 的共用預設值——不在
    這裡快取或複製共用內容，避免覆寫與共用預設的資料不同步。

    資料形狀刻意鏡射 `features.models.FeatureNodeContent`：DOCX 家族
    （系統設計／系統測試／系統安裝）使用 `body`（散文文字）；教育訓練簡報
    使用 `bullets`（結構化條列陣列）。View 層的驗證邏輯與
    `features/views.py::feature_node_content_detail` 相同的規則，理由也
    相同——覆寫內容不可以是共用內容之外的第三種形狀。

    修改共用的 `FeatureNodeContent` 不會觸碰任何既有的覆寫列——兩者刻意
    互相獨立、沒有任何自動同步或「升級為新共用預設」的機制（見 Issue #1
    Out of Scope：內容覆寫的自動比對／自動升級為共用預設機制）。
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="content_overrides"
    )
    node = models.ForeignKey(
        FeatureNode, on_delete=models.CASCADE, related_name="content_overrides"
    )
    document_type = models.CharField(
        "文件類型", max_length=30, choices=DOCUMENT_TYPE_CHOICES
    )

    # 與 FeatureNodeContent 相同的兩個欄位並存、依 document_type 只使用
    # 其中一個（見 features.models.FeatureNodeContent 的說明）。
    body = models.TextField("覆寫內容文字", blank=True, default="")
    bullets = models.JSONField("覆寫條列項目", blank=True, default=list)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "node", "document_type"],
                name="unique_project_node_document_type_override",
            )
        ]
        ordering = ["project_id", "node_id", "document_type"]

    def __str__(self) -> str:
        return f"{self.project_id} override {self.node_id}/{self.document_type}"


class ProjectCustomSection(models.Model):
    """一個專案專屬的自訂章節——不對應任何 FeatureNode，只屬於這一個專案
    （例如客戶專屬的合規聲明），不影響其他專案（Issue #7 acceptance
    criterion 3）。

    `order` 供使用者手動排序這些自訂章節在文件中出現的相對位置——實際插入
    骨架中的哪個位置由 T5/文件預覽頁的排序機制決定，這裡只負責持久化這個
    專案自己的自訂章節清單與其排序權重。
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="custom_sections"
    )
    title = models.CharField("章節標題", max_length=200)
    content = models.TextField("章節內容", blank=True, default="")
    order = models.IntegerField("排序", default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project_id", "order", "id"]

    def __str__(self) -> str:
        return f"{self.title} (project #{self.project_id})"


def get_effective_content(project: Project, node: FeatureNode, document_type: str) -> dict:
    """回傳 (project, node, document_type) 目前「有效」的內容——若專案有
    設定覆寫則用覆寫，否則落回共用預設（FeatureNodeContent），兩者都沒有
    則回傳空內容。

    這是給 T7（文件組裝）用的查詢入口，讓「這個專案這個節點這個文件類型
    最終要用哪份內容」有一個現成、單一的地方可以問，不需要 T7 自己重新
    判斷「有沒有覆寫」的邏輯。這裡刻意不做 `{{project_variables.xxx}}`
    佔位符替換——那是組裝當下才做的事，屬於 T7 範圍。
    """
    override = ProjectFeatureContentOverride.objects.filter(
        project=project, node=node, document_type=document_type
    ).first()
    if override is not None:
        return {
            "source": "override",
            "body": override.body,
            "bullets": override.bullets,
        }

    default = FeatureNodeContent.objects.filter(
        node=node, document_type=document_type
    ).first()
    if default is not None:
        return {
            "source": "default",
            "body": default.body,
            "bullets": default.bullets,
        }

    return {"source": "none", "body": "", "bullets": []}
