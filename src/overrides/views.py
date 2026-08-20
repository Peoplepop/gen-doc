import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from features.models import (
    BULLET_DOCUMENT_TYPES,
    DOCUMENT_TYPE_CHOICES,
    DOCUMENT_TYPE_CODES,
    PROSE_DOCUMENT_TYPES,
    FeatureNode,
    FeatureNodeContent,
)
from projects.models import Project

from .models import ProjectCustomSection, ProjectFeatureContentOverride, get_effective_content

DOCUMENT_TYPE_LABELS = dict(DOCUMENT_TYPE_CHOICES)


def _require_auth(request):
    """Returns a 401 JsonResponse if the request isn't authenticated,
    otherwise None. 同 projects/features/selections app 的既有慣例：未登入
    無法存取任何內容覆寫／自訂章節 API。"""
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
        # 軟刪除的專案視為「不存在」，同 selections/views.py 的既有慣例。
        return Project.objects.get(pk=pk, is_deleted=False), None
    except Project.DoesNotExist:
        return None, JsonResponse({"detail": "找不到專案"}, status=404)


def _get_node_or_404(node_id):
    try:
        return FeatureNode.objects.get(pk=node_id), None
    except (FeatureNode.DoesNotExist, ValueError, TypeError):
        return None, JsonResponse({"detail": "找不到功能節點"}, status=404)


def _validate_body_bullets(document_type, payload):
    """與 features/views.py::feature_node_content_detail 完全相同的
    prose-vs-bullets 驗證規則——覆寫內容的資料形狀必須跟共用內容一致，
    這裡刻意重用同一套判斷邏輯，不發明第三種形狀。回傳 (body, bullets,
    error_response_or_None)。"""
    if document_type in PROSE_DOCUMENT_TYPES:
        body = payload.get("body", "")
        if not isinstance(body, str):
            return None, None, JsonResponse({"detail": "body 必須是文字"}, status=400)
        return body, [], None

    assert document_type in BULLET_DOCUMENT_TYPES
    bullets_value = payload.get("bullets")
    # 教育訓練內容必須以條列項目（陣列）方式提供，不接受直接貼一整段文字
    # 後自動轉條列——跟共用內容端點的規則相同。
    if not isinstance(bullets_value, list):
        return (
            None,
            None,
            JsonResponse(
                {
                    "detail": (
                        "教育訓練內容須以條列項目（陣列）方式提供，"
                        "不接受直接貼一整段文字"
                    )
                },
                status=400,
            ),
        )
    cleaned_bullets = []
    for item in bullets_value:
        if not isinstance(item, str) or not item.strip():
            return (
                None,
                None,
                JsonResponse({"detail": "條列項目必須是非空白文字"}, status=400),
            )
        cleaned_bullets.append(item.strip())
    return "", cleaned_bullets, None


def _serialize_override_row(override: ProjectFeatureContentOverride) -> dict:
    return {
        "project": override.project_id,
        "node": override.node_id,
        "document_type": override.document_type,
        "document_type_label": DOCUMENT_TYPE_LABELS[override.document_type],
        "body": override.body,
        "bullets": override.bullets,
        "updated_at": override.updated_at.isoformat(),
    }


def _serialize_override_detail(
    project: Project,
    node: FeatureNode,
    document_type: str,
    override: ProjectFeatureContentOverride | None,
    default: FeatureNodeContent | None,
) -> dict:
    """完整的「覆寫 vs 共用預設 vs 目前有效內容」視圖，供 T7 的文件組裝
    未來直接重用，也是這個端點自我驗證「覆寫只影響這個 document_type」的
    依據（見 Issue #7 acceptance criteria 驗證方式：透過讀取端點驗證，
    不只靠內部 DB 狀態斷言）。`effective` 這欄直接重用
    `get_effective_content()`——跟 T7 未來會呼叫的是同一份邏輯，不是這裡
    另外複製一份判斷規則。"""
    has_override = override is not None
    return {
        "project": project.id,
        "node": node.id,
        "document_type": document_type,
        "document_type_label": DOCUMENT_TYPE_LABELS[document_type],
        "has_override": has_override,
        "override": (
            {"body": override.body, "bullets": override.bullets, "updated_at": override.updated_at.isoformat()}
            if has_override
            else None
        ),
        "default": (
            {"body": default.body, "bullets": default.bullets, "updated_at": default.updated_at.isoformat()}
            if default is not None
            else None
        ),
        "effective": get_effective_content(project, node, document_type),
    }


@require_http_methods(["GET"])
def project_content_override_list(request, pk):
    """回傳本專案目前所有已設定的內容覆寫（只列出實際存在覆寫列的
    (node, document_type) 組合，不是笛卡爾展開所有節點 x 四種文件類型）。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    overrides = ProjectFeatureContentOverride.objects.filter(project=project)
    return JsonResponse(
        {"results": [_serialize_override_row(o) for o in overrides]}
    )


@require_http_methods(["GET", "PUT", "DELETE"])
def project_content_override_detail(request, pk, node_id, document_type):
    """GET：回傳這個 (project, node, document_type) 目前的覆寫／共用預設／
    有效內容三方視圖。

    PUT：設定（或覆蓋更新）這個 (project, node, document_type) 的覆寫內容
    ——只影響這一個組合，同節點的其他文件類型、其他專案完全不受影響。

    DELETE：移除這個覆寫，該 (project, node, document_type) 之後即改回
    使用共用預設（不是把共用預設複製一份存進來，而是這個組合已經沒有
    覆寫列了）。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    node, error = _get_node_or_404(node_id)
    if error:
        return error

    if document_type not in DOCUMENT_TYPE_CODES:
        return JsonResponse({"detail": "不支援的文件類型"}, status=404)

    override = ProjectFeatureContentOverride.objects.filter(
        project=project, node=node, document_type=document_type
    ).first()

    if request.method == "GET":
        default = FeatureNodeContent.objects.filter(
            node=node, document_type=document_type
        ).first()
        return JsonResponse(
            _serialize_override_detail(project, node, document_type, override, default)
        )

    if request.method == "DELETE":
        if override is not None:
            override.delete()
        default = FeatureNodeContent.objects.filter(
            node=node, document_type=document_type
        ).first()
        return JsonResponse(
            _serialize_override_detail(project, node, document_type, None, default)
        )

    payload, error = _parse_json(request)
    if error:
        return error

    body, bullets, error = _validate_body_bullets(document_type, payload)
    if error:
        return error

    if override is None:
        override = ProjectFeatureContentOverride(
            project=project, node=node, document_type=document_type
        )
    override.body = body
    override.bullets = bullets
    override.save()

    default = FeatureNodeContent.objects.filter(
        node=node, document_type=document_type
    ).first()
    return JsonResponse(
        _serialize_override_detail(project, node, document_type, override, default)
    )


def _serialize_custom_section(section: ProjectCustomSection) -> dict:
    return {
        "id": section.id,
        "project": section.project_id,
        "title": section.title,
        "content": section.content,
        "order": section.order,
        "created_at": section.created_at.isoformat(),
        "updated_at": section.updated_at.isoformat(),
    }


@require_http_methods(["GET", "POST"])
def project_custom_section_list(request, pk):
    """本專案專屬的自訂章節清單——不對應任何 FeatureNode，只屬於這一個
    專案（Issue #7 acceptance criterion 3）。"""
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    if request.method == "GET":
        sections = ProjectCustomSection.objects.filter(project=project)
        return JsonResponse(
            {"results": [_serialize_custom_section(s) for s in sections]}
        )

    payload, error = _parse_json(request)
    if error:
        return error

    title = (payload.get("title") or "").strip()
    if not title:
        return JsonResponse({"detail": "缺少必填欄位: title"}, status=400)

    section = ProjectCustomSection(
        project=project,
        title=title,
        content=payload.get("content") or "",
        order=payload.get("order") or 0,
    )
    section.save()
    return JsonResponse(_serialize_custom_section(section), status=201)


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def project_custom_section_detail(request, pk, section_id):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    try:
        # 章節必須同時屬於這個專案——避免用別的專案的章節 id 誤改到這裡。
        section = ProjectCustomSection.objects.get(pk=section_id, project=project)
    except ProjectCustomSection.DoesNotExist:
        return JsonResponse({"detail": "找不到自訂章節"}, status=404)

    if request.method == "GET":
        return JsonResponse(_serialize_custom_section(section))

    if request.method == "DELETE":
        section.delete()
        return JsonResponse({"detail": "已刪除"})

    payload, error = _parse_json(request)
    if error:
        return error

    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            return JsonResponse({"detail": "title 為必填"}, status=400)
        section.title = title

    if "content" in payload:
        section.content = payload.get("content") or ""

    if "order" in payload:
        order = payload.get("order")
        if not isinstance(order, int) or isinstance(order, bool):
            return JsonResponse({"detail": "order 必須是整數"}, status=400)
        section.order = order

    section.save()
    return JsonResponse(_serialize_custom_section(section))


# --- 覆寫可視化清單（Issue #7 acceptance criterion 4）---------------------
#
# 這兩個端點掛在 `/api/feature-nodes/<pk>/overrides/...` 底下（見
# gen_doc/urls.py 用另一個 include() 掛在跟 features.urls 相同的
# 'api/feature-nodes/' 前綴下——跟 gen_doc/urls.py 讓 projects.urls 與
# selections.urls 共用 'api/projects/' 前綴是同一個既有慣例），讓管理者在
# 後台編輯某節點的共用內容時，可以查詢「目前有哪些專案對這個節點/這個
# 文件類型設有覆寫」的唯讀清單。
#
# 刻意唯讀、不做任何自動比對或一鍵合併——這只是給人工參考、決定要不要
# 手動去跟催那些專案的清單（見 Issue #1 Out of Scope：內容覆寫的自動比對
# ／自動升級為共用預設機制）。這裡只讀取 FeatureNode（唯讀查詢，不修改
# features app 的任何檔案），符合 T6 範圍邊界「不可修改 src/features/
# （唯讀使用除外）」。


def _serialize_override_project(override: ProjectFeatureContentOverride) -> dict:
    return {
        "id": override.project_id,
        "customer_name": override.project.customer_name,
        "project_name": override.project.project_name,
        "updated_at": override.updated_at.isoformat(),
    }


@require_http_methods(["GET"])
def feature_node_override_list(request, pk):
    """回傳這個節點在全部四種文件類型下，各自目前有哪些專案設有覆寫——
    對應 features/views.py::feature_node_content_list 的「列出全部四種
    文件類型」形狀，方便後台編輯共用內容頁一次看到四種類型各自的覆寫
    範圍。沒有任何專案覆寫的文件類型，`projects` 為空陣列（不是 404 或
    省略——空清單本身就是有意義的答案）。"""
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    node, error = _get_node_or_404(pk)
    if error:
        return error

    overrides = ProjectFeatureContentOverride.objects.filter(node=node).select_related(
        "project"
    )
    by_type: dict[str, list] = {code: [] for code in DOCUMENT_TYPE_CODES}
    for override in overrides:
        by_type[override.document_type].append(_serialize_override_project(override))

    results = [
        {
            "document_type": code,
            "document_type_label": DOCUMENT_TYPE_LABELS[code],
            "projects": by_type[code],
        }
        for code in DOCUMENT_TYPE_CODES
    ]
    return JsonResponse({"node": node.id, "results": results})


@require_http_methods(["GET"])
def feature_node_override_list_by_type(request, pk, document_type):
    """同上，但只回傳單一文件類型的覆寫可視化清單——對應
    features/views.py::feature_node_content_detail 的「單一文件類型」形狀。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    node, error = _get_node_or_404(pk)
    if error:
        return error

    if document_type not in DOCUMENT_TYPE_CODES:
        return JsonResponse({"detail": "不支援的文件類型"}, status=404)

    overrides = ProjectFeatureContentOverride.objects.filter(
        node=node, document_type=document_type
    ).select_related("project")

    return JsonResponse(
        {
            "node": node.id,
            "document_type": document_type,
            "document_type_label": DOCUMENT_TYPE_LABELS[document_type],
            "projects": [_serialize_override_project(o) for o in overrides],
        }
    )
