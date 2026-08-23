import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import (
    BULLET_DOCUMENT_TYPES,
    DOCUMENT_TYPE_CHOICES,
    DOCUMENT_TYPE_CODES,
    PROSE_DOCUMENT_TYPES,
    FeatureNode,
    FeatureNodeContent,
)

DOCUMENT_TYPE_LABELS = dict(DOCUMENT_TYPE_CHOICES)


def _require_auth(request):
    """Returns a 401 JsonResponse if the request isn't authenticated,
    otherwise None. Every feature-node endpoint must call this first —
    未登入無法存取任何內容模組管理 API（同 projects app 的既有慣例）。"""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "請先登入"}, status=401)
    return None


def _parse_json(request):
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError:
        return None, JsonResponse({"detail": "無效的 JSON"}, status=400)


def _serialize_node(node: FeatureNode) -> dict:
    return {
        "id": node.id,
        "parent": node.parent_id,
        "name": node.name,
        "description": node.description,
        "created_at": node.created_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
    }


def _serialize_content(node_id: int, document_type: str, content: FeatureNodeContent | None) -> dict:
    if content is None:
        return {
            "node": node_id,
            "document_type": document_type,
            "document_type_label": DOCUMENT_TYPE_LABELS[document_type],
            "body": "",
            "bullets": [],
            "updated_at": None,
        }
    return {
        "node": node_id,
        "document_type": document_type,
        "document_type_label": DOCUMENT_TYPE_LABELS[document_type],
        "body": content.body,
        "bullets": content.bullets,
        "updated_at": content.updated_at.isoformat(),
    }


def _would_create_cycle(node: FeatureNode, new_parent: FeatureNode) -> bool:
    """True if setting node.parent = new_parent would make node its own
    ancestor (directly or transitively). Only relevant on update — a
    freshly-created node can't yet be anyone's ancestor."""
    if new_parent.pk == node.pk:
        return True
    seen = set()
    current = new_parent
    while current.parent_id is not None:
        if current.parent_id == node.pk:
            return True
        if current.parent_id in seen:
            break  # defensive guard against already-corrupt data
        seen.add(current.parent_id)
        current = current.parent
    return False


def _resolve_parent(payload, *, field_present):
    """Resolves the `parent` field from payload. Returns (parent_or_None,
    error_response_or_None). `field_present` tells us whether the caller
    should even attempt to touch the field (PATCH semantics)."""
    if not field_present:
        return None, None, False

    raw = payload.get("parent")
    if raw is None:
        return None, None, True

    try:
        parent = FeatureNode.objects.get(pk=raw)
    except (FeatureNode.DoesNotExist, TypeError, ValueError):
        return None, JsonResponse({"detail": "parent 節點不存在"}, status=400), True
    return parent, None, True


@require_http_methods(["GET", "POST"])
def feature_node_list(request):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    if request.method == "GET":
        nodes = FeatureNode.objects.all()
        return JsonResponse({"results": [_serialize_node(n) for n in nodes]})

    payload, error = _parse_json(request)
    if error:
        return error

    name = (payload.get("name") or "").strip()
    if not name:
        return JsonResponse({"detail": "缺少必填欄位: name"}, status=400)

    parent, error, _ = _resolve_parent(payload, field_present="parent" in payload)
    if error:
        return error

    node = FeatureNode(
        name=name,
        description=payload.get("description") or "",
        parent=parent,
    )
    node.save()
    return JsonResponse(_serialize_node(node), status=201)


@require_http_methods(["GET"])
def feature_node_tree(request):
    """回傳巢狀樹狀結構，供「未來功能選擇」（T4）之類的可選清單使用。

    節點刪除採真實刪除，DB 裡目前存在的節點就是完整可選清單，這裡直接
    回傳全部節點組成的樹，不需要另外過濾。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    nodes = list(FeatureNode.objects.all())

    by_id = {n.id: n for n in nodes}
    serialized = {n.id: {**_serialize_node(n), "children": []} for n in nodes}

    roots = []
    for node in nodes:
        entry = serialized[node.id]
        if node.parent_id is not None and node.parent_id in by_id:
            serialized[node.parent_id]["children"].append(entry)
        else:
            # 根節點。
            roots.append(entry)

    return JsonResponse({"results": roots})


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def feature_node_detail(request, pk):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    try:
        node = FeatureNode.objects.get(pk=pk)
    except FeatureNode.DoesNotExist:
        return JsonResponse({"detail": "找不到功能節點"}, status=404)

    if request.method == "GET":
        return JsonResponse(_serialize_node(node))

    if request.method == "DELETE":
        # 真實刪除，不可復原。內容（FeatureNodeContent）與各專案對這個
        # 節點的勾選/排除紀錄會透過 CASCADE 一併刪除。`parent` 為
        # on_delete=PROTECT——若這個節點仍有子孫，這裡會拋出
        # ProtectedError（未被攔截，直接以 500 呈現），呼叫端須先刪除或
        # 改接子孫節點。
        node.delete()
        return JsonResponse({"detail": "已刪除"})

    payload, error = _parse_json(request)
    if error:
        return error

    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            return JsonResponse({"detail": "name 為必填"}, status=400)
        node.name = name

    if "description" in payload:
        node.description = payload.get("description") or ""

    if "parent" in payload:
        parent, error, _ = _resolve_parent(payload, field_present=True)
        if error:
            return error
        if parent is not None and _would_create_cycle(node, parent):
            return JsonResponse(
                {"detail": "parent 不可設為自己或自己的子孫節點"}, status=400
            )
        node.parent = parent

    node.save()
    return JsonResponse(_serialize_node(node))


@require_http_methods(["GET"])
def feature_node_content_list(request, pk):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    try:
        node = FeatureNode.objects.get(pk=pk)
    except FeatureNode.DoesNotExist:
        return JsonResponse({"detail": "找不到功能節點"}, status=404)

    existing = {c.document_type: c for c in node.contents.all()}
    results = [
        _serialize_content(node.id, code, existing.get(code))
        for code in DOCUMENT_TYPE_CODES
    ]
    return JsonResponse({"results": results})


@require_http_methods(["GET", "PUT", "PATCH"])
def feature_node_content_detail(request, pk, document_type):
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    try:
        node = FeatureNode.objects.get(pk=pk)
    except FeatureNode.DoesNotExist:
        return JsonResponse({"detail": "找不到功能節點"}, status=404)

    if document_type not in DOCUMENT_TYPE_CODES:
        return JsonResponse({"detail": "不支援的文件類型"}, status=404)

    content = FeatureNodeContent.objects.filter(
        node=node, document_type=document_type
    ).first()

    if request.method == "GET":
        return JsonResponse(_serialize_content(node.id, document_type, content))

    payload, error = _parse_json(request)
    if error:
        return error

    if document_type in PROSE_DOCUMENT_TYPES:
        body = payload.get("body", "")
        if not isinstance(body, str):
            return JsonResponse({"detail": "body 必須是文字"}, status=400)
        bullets_value = []
    else:
        assert document_type in BULLET_DOCUMENT_TYPES
        bullets_value = payload.get("bullets")
        # 教育訓練內容必須以條列項目（陣列）方式提供，不接受直接貼一整段
        # 文字後自動轉條列——所以這裡刻意只接受 list，字串一律視為錯誤，
        # 不做任何「自動拆行/自動轉條列」的轉換。
        if not isinstance(bullets_value, list):
            return JsonResponse(
                {
                    "detail": (
                        "教育訓練內容須以條列項目（陣列）方式提供，"
                        "不接受直接貼一整段文字"
                    )
                },
                status=400,
            )
        cleaned_bullets = []
        for item in bullets_value:
            if not isinstance(item, str) or not item.strip():
                return JsonResponse(
                    {"detail": "條列項目必須是非空白文字"}, status=400
                )
            cleaned_bullets.append(item.strip())
        bullets_value = cleaned_bullets
        body = ""

    if content is None:
        content = FeatureNodeContent(node=node, document_type=document_type)

    content.body = body
    content.bullets = bullets_value
    content.save()

    return JsonResponse(_serialize_content(node.id, document_type, content))
