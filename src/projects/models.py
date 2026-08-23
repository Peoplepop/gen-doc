from django.conf import settings
from django.db import models


class Project(models.Model):
    """A single customer acceptance-document project (T2 scope only).

    Carries the 專案變數 (project variable fields) listed in Issue #1's
    Core Domain Model / the T2 acceptance criteria, an owner
    (`owner_user_id` per the spec — modeled here as a FK to the built-in
    User model), and `updated_at` which doubles as the optimistic-lock
    version stamp (compared on every save).

    刪除採真實刪除（無 soft-delete）——刪除一筆 Project 會透過
    `GeneratedDocument.project` 的 `on_delete=CASCADE` 一併永久刪除該專案
    的所有文件產生歷史，且不可復原。

    Everything past T2 (FeatureNode selection, screenshots, content
    overrides, custom sections, document generation, duplication) is
    explicitly out of scope for this model — see the T2 issue body.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="projects",
        verbose_name="建立者",
    )

    # --- 專案變數 (project variables) ---
    customer_name = models.CharField("客戶名稱", max_length=200)
    project_name = models.CharField("專案名稱", max_length=200)
    system_name = models.CharField("系統名稱", max_length=200, blank=True, default="")
    system_url = models.CharField("系統網址", max_length=500, blank=True, default="")
    system_version = models.CharField("系統版本", max_length=100, blank=True, default="")
    acceptance_date = models.DateField("驗收日期", null=True, blank=True)
    install_environment = models.CharField("安裝環境", max_length=200, blank=True, default="")
    operating_system = models.CharField("作業系統", max_length=200, blank=True, default="")
    database = models.CharField("資料庫", max_length=200, blank=True, default="")
    server_location = models.CharField("伺服器位置", max_length=200, blank=True, default="")

    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    # 樂觀鎖版本戳記：每次 save() 都會自動更新；PUT/PATCH 時比對呼叫端
    # 帶來的舊值與目前 DB 值，不一致即代表資料已被他人更新過。
    updated_at = models.DateTimeField("最後更新時間", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "專案"
        verbose_name_plural = "專案"

    def __str__(self) -> str:
        return f"{self.customer_name} / {self.project_name} (#{self.pk})"


# 建立/編輯 Project 時允許讀寫的「專案變數」欄位名稱清單，供 views.py 共用。
PROJECT_VARIABLE_FIELDS = [
    "customer_name",
    "project_name",
    "system_name",
    "system_url",
    "system_version",
    "acceptance_date",
    "install_environment",
    "operating_system",
    "database",
    "server_location",
]
