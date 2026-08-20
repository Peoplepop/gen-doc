from django.db import models

from features.models import FeatureNode
from projects.models import Project


class ProjectFeatureSelection(models.Model):
    """一個專案「勾選」了某個 FeatureNode，作為功能選擇的一個 root。

    勾選一個節點時，其所有子孫節點自動一併納入該專案的功能選擇——但
    這張表本身只記錄「使用者直接勾選了哪些節點」的原始狀態，不會展開
    子孫；子孫的展開（含排除清單套用後的最終結果）由
    `compute_effective_selection()` 在查詢當下即時運算，不落地存成另一
    張表，避免與樹狀結構本身或排除清單產生資料不同步的風險（見 Issue #5
    acceptance criteria：勾選「登入」時其所有子孫自動被包含）。

    設計判斷：`node` 在寫入當下必須是 `is_enabled=True`（見
    `selections/views.py::project_feature_selection` 的驗證）——已停用節點
    不可被新勾選，對應 T3 acceptance criteria「新專案的功能選擇頁看不到、
    無法新勾選已停用節點」。
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="feature_selections"
    )
    node = models.ForeignKey(FeatureNode, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "node"], name="unique_project_checked_node"
            )
        ]
        ordering = ["node_id"]

    def __str__(self) -> str:
        return f"{self.project_id} checked {self.node_id}"


class ProjectFeatureExclusion(models.Model):
    """一個專案把某個子孫節點加入排除清單。

    即使該節點落在某個已勾選節點的子孫範圍內，仍不算作本專案已選功能。
    排除只移除「這一個」節點本身，不會連帶排除它自己的子孫——若子孫也
    要排除，需個別加入排除清單（見 Issue #5 acceptance criteria：排除
    「刪除帳號確認」後，「帳號管理」其餘子孫仍算已選）。
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="feature_exclusions"
    )
    node = models.ForeignKey(FeatureNode, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "node"], name="unique_project_excluded_node"
            )
        ]
        ordering = ["node_id"]

    def __str__(self) -> str:
        return f"{self.project_id} excluded {self.node_id}"


def compute_effective_selection(project: Project) -> list[int]:
    """回傳 `project` 目前「有效已選」的 FeatureNode id 清單（已排序）。

    運算方式：以每個直接勾選（ProjectFeatureSelection）的節點為 root，
    展開其完整子孫子樹（root 本身也算在內），聯集起來，再扣掉：
      1. 排除清單（ProjectFeatureExclusion）上的節點 id；
      2. 目前已停用（is_enabled=False）的節點——即使它曾經是某個已勾選
         節點的子孫，停用後也不再算作有效已選（設計判斷，詳見
         `docs` 或 PR 說明：這是 T4 這個「即時查詢」端點的規則，跟
         `GeneratedDocument` 快照的歷史內容不受影響是兩件事，互不衝突）。

    排除只移除該節點本身，不會連帶砍掉它自己的子孫——子孫仍在聯集結果
    中，除非它們也各自被排除或停用。
    """
    checked_ids = list(
        ProjectFeatureSelection.objects.filter(project=project).values_list(
            "node_id", flat=True
        )
    )
    if not checked_ids:
        return []

    excluded_ids = set(
        ProjectFeatureExclusion.objects.filter(project=project).values_list(
            "node_id", flat=True
        )
    )

    # 一次性把整個功能樹的 parent/child 關係讀進記憶體：功能節點樹是內容
    # 管理規模（幾十到幾百筆），不是大數據，換多次遞迴查詢的複雜度不划算。
    children_by_parent: dict[int | None, list[int]] = {}
    enabled_by_id: dict[int, bool] = {}
    for node_id, parent_id, is_enabled in FeatureNode.objects.values_list(
        "id", "parent_id", "is_enabled"
    ):
        children_by_parent.setdefault(parent_id, []).append(node_id)
        enabled_by_id[node_id] = is_enabled

    subtree_ids: set[int] = set()
    stack = list(checked_ids)
    while stack:
        current = stack.pop()
        if current in subtree_ids:
            continue
        subtree_ids.add(current)
        stack.extend(children_by_parent.get(current, []))

    effective_ids = {
        node_id
        for node_id in subtree_ids
        if enabled_by_id.get(node_id, False) and node_id not in excluded_ids
    }
    return sorted(effective_ids)
