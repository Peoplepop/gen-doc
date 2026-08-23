from django.db import models

# 四種文件類型（見 Issue #1 Core Domain Model / DOCX、PPTX Output Spec）。
# 三類 DOCX 文件（系統設計／系統測試／系統安裝）使用散文文字內容；
# 教育訓練簡報（PPTX）使用結構化條列陣列，兩者資料形狀不同，且不做互轉。
DOCUMENT_TYPE_SYSTEM_DESIGN = "system_design"
DOCUMENT_TYPE_SYSTEM_TEST = "system_test"
DOCUMENT_TYPE_SYSTEM_INSTALL = "system_install"
DOCUMENT_TYPE_TRAINING_DECK = "training_deck"

DOCUMENT_TYPE_CHOICES = [
    (DOCUMENT_TYPE_SYSTEM_DESIGN, "系統設計文件"),
    (DOCUMENT_TYPE_SYSTEM_TEST, "系統測試文件"),
    (DOCUMENT_TYPE_SYSTEM_INSTALL, "系統安裝文件"),
    (DOCUMENT_TYPE_TRAINING_DECK, "教育訓練簡報"),
]

# DOCX 家族的三種文件類型使用散文文字（`body`）；教育訓練簡報使用結構化
# 條列陣列（`bullets`）。這兩組刻意分開，View 層依 document_type 決定要讀寫
# 哪一個欄位，避免把「一大段文字」跟「一組條列項目」誤用同一種資料形狀。
PROSE_DOCUMENT_TYPES = {
    DOCUMENT_TYPE_SYSTEM_DESIGN,
    DOCUMENT_TYPE_SYSTEM_TEST,
    DOCUMENT_TYPE_SYSTEM_INSTALL,
}
BULLET_DOCUMENT_TYPES = {DOCUMENT_TYPE_TRAINING_DECK}

DOCUMENT_TYPE_CODES = [code for code, _label in DOCUMENT_TYPE_CHOICES]


class FeatureNode(models.Model):
    """功能模組節點：自我參照樹狀結構（`parent`，深度不限）。

    取代原始需求書建議的「功能分類」欄位——分類語意完全由 `parent` 表達
    （見 Issue #1 Core Domain Model / Implementation Decisions）。

    刪除採真實刪除（無 soft-delete）：刪除節點會透過關聯的
    `on_delete=CASCADE` 一併刪除其內容（`FeatureNodeContent`）與各專案對
    它的勾選/排除紀錄，且不可復原。`parent` 使用 `on_delete=PROTECT`——
    節點仍有子孫時不可刪除，必須先刪除或改接子孫節點，避免整棵子樹被
    連帶清除或憑空斷開。
    """

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )

    name = models.CharField("功能名稱", max_length=200)
    description = models.TextField("功能說明", blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "功能節點"
        verbose_name_plural = "功能節點"

    def __str__(self) -> str:
        return f"{self.name} (#{self.pk})"


class FeatureNodeContent(models.Model):
    """某個 FeatureNode 在某個文件類型下的共用預設內容。

    以 (node, document_type) 為鍵值儲存 —— 每個節點最多四筆，分別對應
    系統設計／系統測試／系統安裝／教育訓練簡報。DOCX 三類文件用 `body`
    （散文文字）；教育訓練簡報用 `bullets`（結構化條列陣列，JSON list of
    str）。兩個欄位刻意並存於同一個 model 但依 document_type 只使用其中
    一個，View 層負責依 document_type 驗證/清空另一個欄位，避免把整段
    文字誤存進條列欄位、或反過來自動把條列轉成文字。
    """

    node = models.ForeignKey(
        FeatureNode, on_delete=models.CASCADE, related_name="contents"
    )
    document_type = models.CharField(
        "文件類型", max_length=30, choices=DOCUMENT_TYPE_CHOICES
    )

    # DOCX 家族（系統設計／系統測試／系統安裝）使用的散文文字內容。
    body = models.TextField("內容文字", blank=True, default="")

    # 教育訓練簡報（PPTX）使用的結構化條列內容：一組條列項目字串陣列。
    # 刻意使用 JSONField（而非 TextField）以保持「陣列」這個資料形狀，
    # 不接受把一整段文字丟進來後自動切成條列。
    bullets = models.JSONField("條列項目", blank=True, default=list)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["node", "document_type"], name="unique_node_document_type"
            )
        ]
        ordering = ["node_id", "document_type"]
        verbose_name = "功能節點內容"
        verbose_name_plural = "功能節點內容"

    def __str__(self) -> str:
        return f"{self.node.name} / {self.get_document_type_display()}"
