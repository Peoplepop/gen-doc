from django.core.files.base import ContentFile
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from assembly.models import assemble_document, validate_generation_readiness
from features.models import DOCUMENT_TYPE_CODES
from projects.models import Project

from .docx_builder import build_docx
from .models import DOCX_GENERATABLE_DOCUMENT_TYPES, GeneratedDocument

# 固定順序（依 `features.DOCUMENT_TYPE_CHOICES` 宣告順序，排除
# training_deck）——`DOCX_GENERATABLE_DOCUMENT_TYPES` 本身是 set（沿用
# `features.PROSE_DOCUMENT_TYPES`），直接疊代 set 的順序不保證跨程序穩
# 定；狀態彙總端點的回傳欄位順序用這份固定清單，讓回應形狀可預期。
_DOCX_DOCUMENT_TYPES_ORDERED = [
    code for code in DOCUMENT_TYPE_CODES if code in DOCX_GENERATABLE_DOCUMENT_TYPES
]

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _require_auth(request):
    """Returns a 401 JsonResponse if the request isn't authenticated,
    otherwise None. 同 projects/features/selections/overrides/screenshots/
    assembly app 的既有慣例：未登入無法存取任何文件產生／歷史查詢 API。"""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "請先登入"}, status=401)
    return None


def _get_project_or_404(pk):
    try:
        # 軟刪除的專案視為「不存在」，同其他 app 的既有慣例。
        return Project.objects.get(pk=pk, is_deleted=False), None
    except Project.DoesNotExist:
        return None, JsonResponse({"detail": "找不到專案"}, status=404)


def _check_known_document_type(document_type):
    if document_type not in DOCUMENT_TYPE_CODES:
        return JsonResponse({"detail": "不支援的文件類型"}, status=404)
    return None


def _check_generatable_document_type(document_type):
    """T8 只支援 DOCX 三類文件的「產生」動作——教育訓練簡報（PPTX）是 T9
    範圍。刻意跟 `_check_known_document_type` 分開兩層檢查：一個不存在的
    document_type 代碼是 404（路由層級的「這是什麼」錯誤），一個存在但
    「這票還沒做」的 document_type 是 400（業務邏輯層級的「還不支援這個操
    作」錯誤），兩者語意不同，不應該用同一個狀態碼。"""
    error = _check_known_document_type(document_type)
    if error:
        return error
    if document_type not in DOCX_GENERATABLE_DOCUMENT_TYPES:
        return JsonResponse(
            {"detail": "此文件類型尚未支援產生（教育訓練簡報／PPTX 屬於後續票的範圍）"},
            status=400,
        )
    return None


def _serialize_history_entry(record: GeneratedDocument, *, is_latest: bool) -> dict:
    return {
        "id": record.id,
        "project": record.project_id,
        "document_type": record.document_type,
        "output_format": record.output_format,
        "generated_at": record.generated_at.isoformat(),
        "generated_by": record.generated_by_id,
        "is_latest": is_latest,
        "content_snapshot": record.content_snapshot,
        "file_url": record.file.url if record.file else None,
    }


@require_http_methods(["POST"])
def generate_document(request, pk, document_type):
    """產生一份 DOCX 檔案並下載，同時建立一筆新的 `GeneratedDocument`
    歷史紀錄（append-only，不覆蓋先前紀錄——見 Issue #9 What to build／
    Issue #1 Version Strategy）。

    產生前一律先呼叫 `validate_generation_readiness()`（reuse T7，不重新
    實作驗證邏輯）：只要有缺少必要截圖或缺少變數值，一律擋下產生動作，回
    傳明確列出缺漏項目的錯誤（Issue #9 What to build：「必須呼叫
    validate_generation_readiness() 並在不 ready 時擋下產生」）。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    dt_error = _check_generatable_document_type(document_type)
    if dt_error:
        return dt_error

    readiness = validate_generation_readiness(project, document_type)
    if not readiness["ready"]:
        return JsonResponse(
            {
                "detail": "缺少必要截圖或專案變數，無法產生文件",
                "missing_screenshots": readiness["missing_screenshots"],
                "missing_variables": readiness["missing_variables"],
            },
            status=400,
        )

    # 產生當下先把組裝結果讀出來、立刻做成快照——之後即使共用內容或專案
    # 覆寫被修改，這筆紀錄的 content_snapshot 已經是這個時間點的靜態值，
    # 不會再變動（見 Issue #1 Version Strategy／models.py 的
    # GeneratedDocument docstring）。
    assembled = assemble_document(project, document_type)
    generated_at = timezone.now()
    docx_bytes = build_docx(assembled, project, generated_at)

    record = GeneratedDocument(
        project=project,
        document_type=document_type,
        output_format="docx",
        content_snapshot=assembled,
        generated_by=request.user,
    )
    record.file.save(
        f"{document_type}_project{project.id}.docx",
        ContentFile(docx_bytes),
        save=False,
    )
    record.save()

    response = HttpResponse(docx_bytes, content_type=DOCX_CONTENT_TYPE)
    response["Content-Disposition"] = (
        f'attachment; filename="{document_type}_project{project.id}.docx"'
    )
    # 方便呼叫端（前端／測試）不需要再另外呼叫歷史查詢端點，就能拿到這次
    # 產生對應的歷史紀錄 id——非必要但低成本的附加資訊，不影響檔案本身
    # 是這個 HTTP 回應唯一的「主要內容」這件事。
    response["X-Generated-Document-Id"] = str(record.id)
    return response


@require_http_methods(["GET"])
def document_history(request, pk, document_type):
    """查詢一個專案某文件類型的完整產生歷史，全部紀錄一次回傳（含各自的
    完整內容快照與產生時間），依產生時間新到舊排序；`results[0]`（若存在）
    即為「目前版本」——`is_latest` 旗標明講這件事，介面預設只呈現這一筆
    （Issue #9 acceptance criteria 3）。

    設計判斷：這裡刻意不另外拆一個「只給最新一筆」的獨立端點——這個 app
    的預期使用情境（內部工具、單一專案的產生歷史筆數不多）下，一次把全部
    歷史連同快照回傳，讓前端自己決定要不要展開查看舊版本，比多一個端點、
    多一次來回請求更簡單。`document_type` 刻意接受全部四種合法代碼（不限
    於 T8 支援產生的三種 DOCX 類型）——歷史查詢本身是通用能力，T9 的
    training_deck 一旦開始建立 GeneratedDocument 紀錄，同一個端點不需要
    修改就能查詢，只是在那之前，training_deck 的查詢結果永遠是空清單。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    dt_error = _check_known_document_type(document_type)
    if dt_error:
        return dt_error

    records = list(
        GeneratedDocument.objects.filter(project=project, document_type=document_type)
    )
    results = [
        _serialize_history_entry(record, is_latest=(index == 0))
        for index, record in enumerate(records)
    ]
    return JsonResponse({"results": results})


@require_http_methods(["GET"])
def project_document_status_list(request):
    """專案列表用：每個（未軟刪除的）專案，這三類 DOCX 文件各自是否已產
    生過至少一次（Issue #9 acceptance criteria 4）。

    設計判斷（按 Issue #9 要求誠實標註）：刻意做成 `documents` app 自己的
    獨立端點（`GET /api/projects/documents-status/`），而不是擴充既有的
    `projects.views.project_list`／`projects/urls.py`。理由：
    - Issue #9 本文沒有明講要用哪一種做法（「likely extending projects'
      list endpoint, or a dedicated one — check the issue」），本身就是留
      給本票判斷的空間；
    - 同一時間有另一張並行的票（專案複製，T10）也在修改
      `projects/views.py`／`projects/urls.py`，把本票的變更完全隔離在
      `documents` app 自己的檔案裡，可以讓兩張票的變更零重疊，不需要協調
      誰先合併、也不會在 merge 時互相衝突；
    - 語意上這個端點回傳的是「文件產生狀態」的彙總，本來就更貼近
      `documents` app 的職責，而不是 `projects` app 的職責。
    - 只回傳 T8 支援產生的三種 DOCX 類型（`system_design`／`system_test`／
      `system_install`）——Issue #9 acceptance criterion 4 原文明講「這三
      類」。`training_deck` 目前不在這個端點的回傳形狀裡；等 T9 落地後，
      要嘛在這裡加一個 `training_deck` 欄位（那時候 `GeneratedDocument`
      應該已經有 training_deck 的紀錄可查），要嘛由 T9 自行決定要不要沿
      用這個端點，這裡不預先臆測 T9 的介面需求。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    projects = list(Project.objects.filter(is_deleted=False))
    project_ids = [p.id for p in projects]

    generated_pairs = set(
        GeneratedDocument.objects.filter(
            project_id__in=project_ids,
            document_type__in=DOCX_GENERATABLE_DOCUMENT_TYPES,
        ).values_list("project_id", "document_type")
    )

    results = []
    for project in projects:
        entry = {"project": project.id}
        for document_type in _DOCX_DOCUMENT_TYPES_ORDERED:
            entry[document_type] = (project.id, document_type) in generated_pairs
        results.append(entry)

    return JsonResponse({"results": results})
