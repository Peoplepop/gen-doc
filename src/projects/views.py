import json
from datetime import datetime

from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import PROJECT_VARIABLE_FIELDS, Project

REQUIRED_FIELDS = ("customer_name", "project_name")
DATE_FIELDS = ("acceptance_date",)


def _require_auth(request):
    """Returns a 401 JsonResponse if the request isn't authenticated,
    otherwise None. Every project endpoint must call this first — 未登入
    無法存取任何專案 API."""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "請先登入"}, status=401)
    return None


def _parse_json(request):
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError:
        return None, JsonResponse({"detail": "無效的 JSON"}, status=400)


def _serialize(project: Project) -> dict:
    data = {
        "id": project.id,
        "owner": project.owner_id,
        "is_deleted": project.is_deleted,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }
    for field in PROJECT_VARIABLE_FIELDS:
        value = getattr(project, field)
        if field in DATE_FIELDS:
            data[field] = value.isoformat() if value else None
        else:
            data[field] = value
    return data


def _apply_variable_fields(project: Project, payload: dict, *, partial: bool):
    """Copies 專案變數 fields from payload onto project.

    When partial=True (PATCH/PUT of an existing project), only fields
    present in payload are touched. Returns an error JsonResponse, or None.
    """
    for field in PROJECT_VARIABLE_FIELDS:
        if partial and field not in payload:
            continue

        if field in DATE_FIELDS:
            raw = payload.get(field)
            if not raw:
                setattr(project, field, None)
                continue
            try:
                setattr(project, field, datetime.strptime(raw, "%Y-%m-%d").date())
            except ValueError:
                return JsonResponse(
                    {"detail": f"{field} 格式須為 YYYY-MM-DD"}, status=400
                )
            continue

        setattr(project, field, payload.get(field) or "")

    return None


@csrf_exempt
@require_http_methods(["GET", "POST"])
def project_list(request):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    if request.method == "GET":
        # 軟刪除的專案不出現在列表中，但資料本身仍留在 DB。
        projects = Project.objects.filter(is_deleted=False)
        return JsonResponse({"results": [_serialize(p) for p in projects]})

    payload, error = _parse_json(request)
    if error:
        return error

    missing = [f for f in REQUIRED_FIELDS if not (payload.get(f) or "").strip()]
    if missing:
        return JsonResponse(
            {"detail": f"缺少必填欄位: {', '.join(missing)}"}, status=400
        )

    project = Project(owner=request.user)
    error = _apply_variable_fields(project, payload, partial=False)
    if error:
        return error

    project.save()
    return JsonResponse(_serialize(project), status=201)


@csrf_exempt
@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def project_detail(request, pk):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    try:
        # 軟刪除的專案視為「不存在」於一般操作流程中。
        project = Project.objects.get(pk=pk, is_deleted=False)
    except Project.DoesNotExist:
        return JsonResponse({"detail": "找不到專案"}, status=404)

    if request.method == "GET":
        return JsonResponse(_serialize(project))

    if request.method == "DELETE":
        project.is_deleted = True
        project.save(update_fields=["is_deleted", "updated_at"])
        return JsonResponse({"detail": "已刪除"})

    # PUT/PATCH：先做樂觀鎖檢查，再套用欄位變更。
    payload, error = _parse_json(request)
    if error:
        return error

    client_updated_at_raw = payload.get("updated_at")
    if not client_updated_at_raw:
        return JsonResponse(
            {"detail": "缺少 updated_at，無法判斷是否為最新版本"}, status=400
        )

    client_updated_at = parse_datetime(client_updated_at_raw)
    if client_updated_at is None:
        return JsonResponse({"detail": "updated_at 格式錯誤"}, status=400)

    if client_updated_at != project.updated_at:
        # Given 使用者 A 與使用者 B 同時開啟同一個專案並各自修改，
        # When A 先儲存成功、B 接著儲存，
        # Then B 的儲存被系統擋下並提示「已被其他人更新，請重新整理」。
        return JsonResponse(
            {"detail": "此專案已被其他人更新，請重新整理"}, status=409
        )

    error = _apply_variable_fields(project, payload, partial=True)
    if error:
        return error

    if not project.customer_name.strip() or not project.project_name.strip():
        return JsonResponse(
            {"detail": "customer_name 與 project_name 為必填"}, status=400
        )

    project.save()
    return JsonResponse(_serialize(project))
