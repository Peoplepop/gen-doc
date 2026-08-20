import json
import os

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from features.models import DOCUMENT_TYPE_CODES, FeatureNode
from projects.models import Project
from selections.models import compute_effective_selection

from .models import ProjectScreenshot, ScreenshotRequirement, resolve_applicable_screenshot

# 允許的圖片副檔名／magic bytes 簽章。刻意不用 Pillow（見 models.py 的
# ImageField vs FileField 判斷）——用副檔名 + 檔案內容前幾個 bytes 的
# 簽章雙重驗證，抓「把 .txt 改副檔名假裝是圖片」這類情況（Issue #6
# acceptance criterion 5：上傳失敗需顯示明確錯誤訊息）。
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _looks_like_image(header: bytes) -> bool:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if header.startswith(b"\xff\xd8\xff"):
        return True
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return True
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return True
    return False


def _validate_image_upload(uploaded_file) -> str | None:
    """回傳錯誤訊息字串（None 代表通過驗證）。同時檢查副檔名與檔案內容
    的 magic bytes，避免只信任副檔名或前端宣稱的 content-type。"""
    name = uploaded_file.name or ""
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return (
            f"不支援的檔案格式: {ext or '(無副檔名)'}，"
            "僅接受 PNG／JPG／JPEG／GIF／WEBP 圖片"
        )

    header = uploaded_file.read(16)
    uploaded_file.seek(0)
    if not _looks_like_image(header):
        return "檔案內容不是有效的圖片格式，請確認檔案未毀損或副檔名正確"
    return None


def _require_auth(request):
    """Returns a 401 JsonResponse if the request isn't authenticated,
    otherwise None. 同 selections/features/projects app 的既有慣例：未登
    入無法存取任何截圖需求／截圖 API。"""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "請先登入"}, status=401)
    return None


def _parse_json(request):
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError:
        return None, JsonResponse({"detail": "無效的 JSON"}, status=400)


def _get_node_or_404(pk):
    try:
        return FeatureNode.objects.get(pk=pk), None
    except FeatureNode.DoesNotExist:
        return None, JsonResponse({"detail": "找不到功能節點"}, status=404)


def _get_requirement_or_404(pk):
    try:
        return ScreenshotRequirement.objects.get(pk=pk), None
    except ScreenshotRequirement.DoesNotExist:
        return None, JsonResponse({"detail": "找不到截圖需求項目"}, status=404)


def _get_project_or_404(pk):
    try:
        # 軟刪除的專案視為「不存在」，同 projects/selections app 的既有慣例。
        return Project.objects.get(pk=pk, is_deleted=False), None
    except Project.DoesNotExist:
        return None, JsonResponse({"detail": "找不到專案"}, status=404)


def _resolve_document_types_for_write(post_data, *, allow_default_all: bool):
    """從 multipart POST data 解析 `document_types`（可重複的表單欄位）。

    `allow_default_all=True`（預設版本上傳）：沒有帶這個欄位時，預設套用
    全部四種文件類型（見 Issue #6 acceptance criterion 3：「預設全選」）。
    `allow_default_all=False`（客製版本上傳）：必須至少指定一種文件類型，
    不套用「預設全選」（客製版本存在的意義就是縮小到特定文件類型）。
    """
    raw = post_data.getlist("document_types")
    if not raw:
        if allow_default_all:
            return list(DOCUMENT_TYPE_CODES), None
        return None, JsonResponse(
            {"detail": "客製版本必須指定至少一個文件類型"}, status=400
        )

    invalid = sorted(set(raw) - set(DOCUMENT_TYPE_CODES))
    if invalid:
        return None, JsonResponse(
            {"detail": f"不支援的文件類型: {invalid}"}, status=400
        )

    # 依 DOCUMENT_TYPE_CODES 的固定順序回傳、去重，不保留呼叫端送入的順序
    # /重複值，讓序列化輸出穩定可預期。
    selected = set(raw)
    return [code for code in DOCUMENT_TYPE_CODES if code in selected], None


def _serialize_requirement(requirement: ScreenshotRequirement) -> dict:
    return {
        "id": requirement.id,
        "node": requirement.node_id,
        "name": requirement.name,
        "order": requirement.order,
        "is_enabled": requirement.is_enabled,
        "created_at": requirement.created_at.isoformat(),
        "updated_at": requirement.updated_at.isoformat(),
    }


def _serialize_screenshot(shot: ProjectScreenshot | None) -> dict | None:
    if shot is None:
        return None
    return {
        "id": shot.id,
        "project": shot.project_id,
        "requirement": shot.requirement_id,
        "is_custom": shot.is_custom,
        "document_types": shot.document_types,
        "image_url": shot.image.url if shot.image else None,
        "original_filename": shot.original_filename,
        "caption": shot.caption,
        "created_at": shot.created_at.isoformat(),
        "updated_at": shot.updated_at.isoformat(),
    }


def _serialize_node_summary(node: FeatureNode) -> dict:
    return {"id": node.id, "name": node.name}


# --- 後台：ScreenshotRequirement CRUD（掛在 FeatureNode 上的截圖需求定義） ---


@require_http_methods(["GET", "POST"])
def screenshot_requirement_list(request, node_pk):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    node, error = _get_node_or_404(node_pk)
    if error:
        return error

    if request.method == "GET":
        include_disabled = request.GET.get("include_disabled") == "true"
        requirements = node.screenshot_requirements.all()
        if not include_disabled:
            requirements = requirements.filter(is_enabled=True)
        return JsonResponse(
            {"results": [_serialize_requirement(r) for r in requirements]}
        )

    payload, error = _parse_json(request)
    if error:
        return error

    name = (payload.get("name") or "").strip()
    if not name:
        return JsonResponse({"detail": "缺少必填欄位: name"}, status=400)

    requirement = ScreenshotRequirement(
        node=node,
        name=name,
        order=payload.get("order") or 0,
        is_enabled=bool(payload.get("is_enabled", True)),
    )
    requirement.save()
    return JsonResponse(_serialize_requirement(requirement), status=201)


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def screenshot_requirement_detail(request, pk):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    requirement, error = _get_requirement_or_404(pk)
    if error:
        return error

    if request.method == "GET":
        return JsonResponse(_serialize_requirement(requirement))

    if request.method == "DELETE":
        # 軟停用：資料列與既有 ProjectScreenshot 關聯不受影響（見
        # models.py 的設計判斷說明）。
        requirement.is_enabled = False
        requirement.save(update_fields=["is_enabled", "updated_at"])
        return JsonResponse({"detail": "已停用"})

    payload, error = _parse_json(request)
    if error:
        return error

    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            return JsonResponse({"detail": "name 為必填"}, status=400)
        requirement.name = name

    if "order" in payload:
        requirement.order = payload.get("order") or 0

    if "is_enabled" in payload:
        requirement.is_enabled = bool(payload.get("is_enabled"))

    requirement.save()
    return JsonResponse(_serialize_requirement(requirement))


# --- 專案端：截圖檢查清單（依有效功能選擇自動衍生） ---


@require_http_methods(["GET"])
def project_screenshot_checklist(request, project_pk):
    """依本專案目前的『有效已選』功能節點（重用
    `selections.compute_effective_selection`，不重新實作選擇邏輯），自動
    列出每個節點定義的所有截圖需求項目，並標示已完成／尚未上傳狀態（見
    Issue #6 acceptance criterion 1）。"""
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(project_pk)
    if error:
        return error

    effective_ids = compute_effective_selection(project)
    if not effective_ids:
        return JsonResponse({"results": []})

    nodes_by_id = {n.id: n for n in FeatureNode.objects.filter(id__in=effective_ids)}
    requirements = list(
        ScreenshotRequirement.objects.filter(
            node_id__in=effective_ids, is_enabled=True
        ).order_by("node_id", "order", "id")
    )
    requirement_ids = [r.id for r in requirements]

    screenshots = ProjectScreenshot.objects.filter(
        project=project, requirement_id__in=requirement_ids
    )
    default_by_requirement: dict[int, ProjectScreenshot] = {}
    customs_by_requirement: dict[int, list[ProjectScreenshot]] = {}
    for shot in screenshots:
        if shot.is_custom:
            customs_by_requirement.setdefault(shot.requirement_id, []).append(shot)
        else:
            default_by_requirement[shot.requirement_id] = shot

    requirements_by_node: dict[int, list[ScreenshotRequirement]] = {}
    for req in requirements:
        requirements_by_node.setdefault(req.node_id, []).append(req)

    results = []
    for node_id in effective_ids:
        node = nodes_by_id.get(node_id)
        node_requirements = requirements_by_node.get(node_id)
        if node is None or not node_requirements:
            continue

        items = []
        for req in node_requirements:
            default_shot = default_by_requirement.get(req.id)
            items.append(
                {
                    "requirement": _serialize_requirement(req),
                    "status": "已完成" if default_shot else "尚未上傳",
                    "default": _serialize_screenshot(default_shot),
                    "customs": [
                        _serialize_screenshot(s)
                        for s in customs_by_requirement.get(req.id, [])
                    ],
                }
            )

        results.append({"node": _serialize_node_summary(node), "items": items})

    return JsonResponse({"results": results})


# --- 專案端：預設版本（套用全部或使用者勾選的文件類型子集合） ---


@require_http_methods(["GET", "POST", "DELETE"])
def project_screenshot_default(request, project_pk, requirement_pk):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(project_pk)
    if error:
        return error
    requirement, error = _get_requirement_or_404(requirement_pk)
    if error:
        return error

    default_shot = ProjectScreenshot.objects.filter(
        project=project, requirement=requirement, is_custom=False
    ).first()

    if request.method == "GET":
        if default_shot is None:
            return JsonResponse({"detail": "尚未上傳"}, status=404)
        return JsonResponse(_serialize_screenshot(default_shot))

    if request.method == "DELETE":
        if default_shot is None:
            return JsonResponse({"detail": "尚未上傳"}, status=404)
        default_shot.image.delete(save=False)
        default_shot.delete()
        return JsonResponse({"detail": "已刪除"})

    # POST：上傳（第一次）或更換（已有預設版本時，直接替換圖片與套用範圍）。
    uploaded = request.FILES.get("image")
    if uploaded is None:
        return JsonResponse({"detail": "缺少檔案: image"}, status=400)

    validation_error = _validate_image_upload(uploaded)
    if validation_error:
        return JsonResponse({"detail": validation_error}, status=400)

    document_types, error = _resolve_document_types_for_write(
        request.POST, allow_default_all=True
    )
    if error:
        return error

    is_create = default_shot is None
    with transaction.atomic():
        if default_shot is None:
            default_shot = ProjectScreenshot(
                project=project, requirement=requirement, is_custom=False
            )
        else:
            default_shot.image.delete(save=False)  # 換圖：先清掉舊檔案

        default_shot.image = uploaded
        default_shot.original_filename = uploaded.name
        default_shot.document_types = document_types
        if "caption" in request.POST:
            default_shot.caption = request.POST.get("caption") or ""
        default_shot.save()

    status_code = 201 if is_create else 200
    return JsonResponse(_serialize_screenshot(default_shot), status=status_code)


# --- 專案端：客製版本（套用在特定文件類型子集合，與預設版本並存） ---


@require_http_methods(["GET", "POST"])
def project_screenshot_custom_list(request, project_pk, requirement_pk):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(project_pk)
    if error:
        return error
    requirement, error = _get_requirement_or_404(requirement_pk)
    if error:
        return error

    if request.method == "GET":
        customs = ProjectScreenshot.objects.filter(
            project=project, requirement=requirement, is_custom=True
        )
        return JsonResponse({"results": [_serialize_screenshot(s) for s in customs]})

    uploaded = request.FILES.get("image")
    if uploaded is None:
        return JsonResponse({"detail": "缺少檔案: image"}, status=400)

    validation_error = _validate_image_upload(uploaded)
    if validation_error:
        return JsonResponse({"detail": validation_error}, status=400)

    document_types, error = _resolve_document_types_for_write(
        request.POST, allow_default_all=False
    )
    if error:
        return error

    already_covered: set[str] = set()
    for shot in ProjectScreenshot.objects.filter(
        project=project, requirement=requirement, is_custom=True
    ):
        already_covered.update(shot.document_types)
    overlap = sorted(already_covered.intersection(document_types))
    if overlap:
        return JsonResponse(
            {
                "detail": (
                    f"這些文件類型已經有客製版本，請先刪除舊版本或改用替換: {overlap}"
                )
            },
            status=400,
        )

    shot = ProjectScreenshot(
        project=project,
        requirement=requirement,
        is_custom=True,
        image=uploaded,
        original_filename=uploaded.name,
        document_types=document_types,
        caption=request.POST.get("caption") or "",
    )
    shot.save()
    return JsonResponse(_serialize_screenshot(shot), status=201)


def _get_custom_shot_or_404(project, requirement, custom_pk):
    try:
        return (
            ProjectScreenshot.objects.get(
                pk=custom_pk, project=project, requirement=requirement, is_custom=True
            ),
            None,
        )
    except ProjectScreenshot.DoesNotExist:
        return None, JsonResponse({"detail": "找不到客製版本"}, status=404)


@require_http_methods(["GET", "POST", "DELETE"])
def project_screenshot_custom_detail(request, project_pk, requirement_pk, custom_pk):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(project_pk)
    if error:
        return error
    requirement, error = _get_requirement_or_404(requirement_pk)
    if error:
        return error
    shot, error = _get_custom_shot_or_404(project, requirement, custom_pk)
    if error:
        return error

    if request.method == "GET":
        return JsonResponse(_serialize_screenshot(shot))

    if request.method == "DELETE":
        shot.image.delete(save=False)
        shot.delete()
        return JsonResponse({"detail": "已刪除"})

    # POST：更換這個客製版本的圖片，並可選擇一併調整套用的文件類型範圍。
    uploaded = request.FILES.get("image")
    if uploaded is not None:
        validation_error = _validate_image_upload(uploaded)
        if validation_error:
            return JsonResponse({"detail": validation_error}, status=400)

    if request.POST.getlist("document_types"):
        document_types, error = _resolve_document_types_for_write(
            request.POST, allow_default_all=False
        )
        if error:
            return error

        already_covered: set[str] = set()
        for other in ProjectScreenshot.objects.filter(
            project=project, requirement=requirement, is_custom=True
        ).exclude(pk=shot.pk):
            already_covered.update(other.document_types)
        overlap = sorted(already_covered.intersection(document_types))
        if overlap:
            return JsonResponse(
                {"detail": f"這些文件類型已經有其他客製版本: {overlap}"}, status=400
            )
        shot.document_types = document_types

    if uploaded is not None:
        shot.image.delete(save=False)
        shot.image = uploaded
        shot.original_filename = uploaded.name

    if "caption" in request.POST:
        shot.caption = request.POST.get("caption") or ""

    shot.save()
    return JsonResponse(_serialize_screenshot(shot))


# --- 查詢：某個文件類型實際套用哪一張圖（客製優先於預設） ---


@require_http_methods(["GET"])
def project_screenshot_resolve(request, project_pk, requirement_pk):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(project_pk)
    if error:
        return error
    requirement, error = _get_requirement_or_404(requirement_pk)
    if error:
        return error

    document_type = request.GET.get("document_type")
    if document_type not in DOCUMENT_TYPE_CODES:
        return JsonResponse(
            {"detail": "缺少或不支援的 document_type 查詢參數"}, status=400
        )

    shot = resolve_applicable_screenshot(project, requirement, document_type)
    return JsonResponse(
        {
            "document_type": document_type,
            "applicable": shot is not None,
            "screenshot": _serialize_screenshot(shot),
        }
    )
