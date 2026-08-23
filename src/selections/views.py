import json

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from features.models import FeatureNode
from projects.models import Project

from .models import (
    ProjectFeatureExclusion,
    ProjectFeatureSelection,
    compute_effective_selection,
)


def _require_auth(request):
    """Returns a 401 JsonResponse if the request isn't authenticated,
    otherwise None. 同 projects/features app 的既有慣例：未登入無法存取
    任何功能選擇 API。"""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "請先登入"}, status=401)
    return None


def _parse_json(request):
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError:
        return None, JsonResponse({"detail": "無效的 JSON"}, status=400)


def _get_project_or_404(pk):
    try:
        return Project.objects.get(pk=pk), None
    except Project.DoesNotExist:
        return None, JsonResponse({"detail": "找不到專案"}, status=404)


def _clean_id_list(payload, field):
    """驗證 payload[field] 是一個整數 id 陣列，回傳 (ids, error)。
    `bool` 在 Python 是 `int` 的子類別，這裡刻意排除，避免 `true`/`false`
    被誤當成 1/0 混進節點 id 清單。"""
    raw = payload.get(field, [])
    if not isinstance(raw, list):
        return None, JsonResponse(
            {"detail": f"{field} 須為節點 id 陣列"}, status=400
        )
    ids = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            return None, JsonResponse(
                {"detail": f"{field} 只能包含節點 id（整數）"}, status=400
            )
        ids.append(item)
    return ids, None


def _serialize_selection(project: Project) -> dict:
    checked_ids = sorted(
        ProjectFeatureSelection.objects.filter(project=project).values_list(
            "node_id", flat=True
        )
    )
    excluded_ids = sorted(
        ProjectFeatureExclusion.objects.filter(project=project).values_list(
            "node_id", flat=True
        )
    )
    return {
        "project": project.id,
        "checked": checked_ids,
        "excluded": excluded_ids,
        "effective": compute_effective_selection(project),
    }


@require_http_methods(["GET", "PUT"])
def project_feature_selection(request, pk):
    """GET：回傳本專案目前的原始勾選狀態（checked/excluded）與即時運算
    出的有效已選清單（effective）。

    PUT：整組覆寫本專案的勾選狀態與排除清單（不是逐一增刪，客戶端每次
    都送出完整的 checked/excluded 陣列）——對應「樹狀勾選」UI 每次都會
    知道目前整棵樹的勾選狀態，比逐一 diff 更不容易產生前後端不同步。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    if request.method == "GET":
        return JsonResponse(_serialize_selection(project))

    payload, error = _parse_json(request)
    if error:
        return error

    checked_ids, error = _clean_id_list(payload, "checked")
    if error:
        return error
    excluded_ids, error = _clean_id_list(payload, "excluded")
    if error:
        return error

    checked_nodes = list(FeatureNode.objects.filter(id__in=checked_ids))
    found_checked_ids = {n.id for n in checked_nodes}
    missing_checked = set(checked_ids) - found_checked_ids
    if missing_checked:
        # 已刪除的節點根本不存在，自然也會落在這個「找不到」分支——節點
        # 刪除採真實刪除（無 soft-delete），不需要另外一條「已停用不可
        # 勾選」的規則。
        return JsonResponse(
            {"detail": f"找不到功能節點: {sorted(missing_checked)}"}, status=400
        )

    excluded_found_ids = set(
        FeatureNode.objects.filter(id__in=excluded_ids).values_list("id", flat=True)
    )
    missing_excluded = set(excluded_ids) - excluded_found_ids
    if missing_excluded:
        return JsonResponse(
            {"detail": f"找不到功能節點: {sorted(missing_excluded)}"}, status=400
        )

    with transaction.atomic():
        ProjectFeatureSelection.objects.filter(project=project).exclude(
            node_id__in=checked_ids
        ).delete()
        for node_id in checked_ids:
            ProjectFeatureSelection.objects.get_or_create(
                project=project, node_id=node_id
            )

        ProjectFeatureExclusion.objects.filter(project=project).exclude(
            node_id__in=excluded_ids
        ).delete()
        for node_id in excluded_ids:
            ProjectFeatureExclusion.objects.get_or_create(
                project=project, node_id=node_id
            )

    return JsonResponse(_serialize_selection(project))
