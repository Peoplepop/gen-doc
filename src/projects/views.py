import json
from datetime import datetime

from django.db import transaction
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

# T10 scope (Issue #11): 專案複製. 這裡刻意透過既有 apps 的 models 直接複製
# 資料列，不重新實作它們各自的驗證/業務邏輯——同 T7 (assembly) 對
# selections/overrides/screenshots 的重用慣例。projects app 對這些 app 的
# 依賴方向是單向的（projects.views -> 這些 app 的 models），不會造成循環
# import：這些 app 的 models.py 只 import `projects.models.Project`，從未
# import `projects.views`。
from assembly.models import (
    SECTION_KEY_CUSTOM_PREFIX,
    ProjectPreviewSetting,
    custom_section_key,
)
from overrides.models import ProjectCustomSection, ProjectFeatureContentOverride
from selections.models import ProjectFeatureExclusion, ProjectFeatureSelection

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


def _remap_section_keys(keys: list[str], custom_section_id_map: dict[int, int]) -> list[str]:
    """把 `ProjectPreviewSetting.section_order` / `excluded_sections` 陣列中
    的 `custom:<id>` key 從舊專案的自訂章節 id 重寫成新專案複製出來的自訂
    章節 id（`node:<id>` key 不受影響，原樣保留——FeatureNode id 是全域共
    用的，複製專案不會產生新的節點 id）。

    複製 `ProjectCustomSection` 時一定會產生全新的 id（自增主鍵），所以舊
    專案預覽設定裡任何指向舊自訂章節 id 的 `custom:<old_id>` key，若不重
    寫就會變成指向新專案上不存在（或指向別的專案）的章節，等於幽靈章節
    設定。若某個舊 id 在複製後找不到對應的新 id（理論上不會發生——自訂
    章節一定跟著同一筆交易一起複製——這裡仍防禦性地直接捨棄該 key，不拋
    例外中斷整個複製流程）。
    """
    remapped = []
    for key in keys:
        if key.startswith(SECTION_KEY_CUSTOM_PREFIX):
            old_id = int(key[len(SECTION_KEY_CUSTOM_PREFIX):])
            new_id = custom_section_id_map.get(old_id)
            if new_id is not None:
                remapped.append(custom_section_key(new_id))
            continue
        remapped.append(key)
    return remapped


@require_http_methods(["POST"])
def project_duplicate(request, pk):
    """複製一個已設定完整的專案（Issue #11 / T10）。

    複製範圍（Issue #1 決策17 / Issue #11 acceptance criteria）：
      - 功能勾選清單（`selections.ProjectFeatureSelection`）
      - 排除清單（`selections.ProjectFeatureExclusion`）
      - 內容覆寫（`overrides.ProjectFeatureContentOverride`）
      - 自訂章節（`overrides.ProjectCustomSection`）——複製後產生全新的
        章節 id，不沿用舊專案的 id。
      - 預覽設定（`assembly.ProjectPreviewSetting`）的 `section_order` /
        `excluded_sections`，但其中引用舊自訂章節 id 的 `custom:<id>` key
        會被重寫成新複製出來的章節 id（見 `_remap_section_keys`）。

    刻意不複製：
      - 截圖（`screenshots.ProjectScreenshot`）——新專案的截圖需求清單
        全部回到「尚未上傳」，這是 `screenshots` app 既有查詢邏輯
        （`project_screenshot_checklist` 依 `ProjectScreenshot` 是否存在
        判斷已完成/尚未上傳）在新專案沒有任何 `ProjectScreenshot` 列時的
        自然結果，不需要這裡額外處理。
      - 專案變數（customer_name／system_url 等）——新專案只使用這個端點
        呼叫端提供的 `customer_name`／`project_name`（因為 `Project` model
        要求這兩欄非空、無法留空儲存，見下方「設計判斷」），其餘所有專案
        變數欄位一律維持 model 預設值（空字串／None），即使 payload 裡帶
        了其他欄位也不採用——確保「專案變數為空」這條驗收標準對除了兩個
        必填欄位以外的所有欄位都成立。
      - 產生歷史——本專案目前的程式碼庫尚未實作 `GeneratedDocument`
        （T8/T9 範圍），沒有東西可複製；這裡完全不引用該 model。
      - `ProjectPreviewSetting.screenshot_selections` /
        `caption_overrides`——這兩個欄位的內容是
        `{截圖需求 id: ProjectScreenshot id}` / `{截圖需求 id: 圖說文字}`，
        會指向舊專案的 `ProjectScreenshot` id。既然截圖本身刻意不複製，
        沿用這些 id 只會讓新專案的預覽設定指向不存在、或（更糟）指向別的
        專案的截圖列。這裡選擇重置成空字典，讓新專案這兩個欄位退回
        `assemble_document()` 的預設行為（自動判斷截圖、使用截圖本身的
        圖說）——這是本票的一個真實灰色地帶判斷，Issue #11 沒有明講，PR
        說明中誠實標註。

    設計判斷（同樣未在 issue 中明講，PR 說明中誠實標註）：`Project` model
    的 `customer_name`／`project_name` 是必填欄位（見 T2 既有的
    `REQUIRED_FIELDS` 驗證），不可能存成空字串；但 Issue #11 acceptance
    criteria 講「新專案的專案變數為空（不繼承原專案的客戶名稱等資料）」。
    這裡採用的解法：呼叫端必須在 request body 提供全新的 `customer_name`／
    `project_name`（沿用 T2 既有的必填驗證規則），視為「複製動作本身需要
    使用者立刻替新專案命名」的必要輸入，而不是從舊專案繼承而來的值；除此
    之外的其餘專案變數欄位一律留空，不接受 payload 帶入、也不從舊專案複製
    ——藉此讓「專案變數為空」這條驗收標準在兩個必填欄位之外都成立。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    try:
        # 軟刪除的專案視為「不存在」，同本檔案其他端點的既有慣例。
        source = Project.objects.get(pk=pk, is_deleted=False)
    except Project.DoesNotExist:
        return JsonResponse({"detail": "找不到專案"}, status=404)

    payload, error = _parse_json(request)
    if error:
        return error

    missing = [f for f in REQUIRED_FIELDS if not (payload.get(f) or "").strip()]
    if missing:
        return JsonResponse(
            {"detail": f"缺少必填欄位: {', '.join(missing)}"}, status=400
        )

    with transaction.atomic():
        new_project = Project(owner=request.user)
        for field in REQUIRED_FIELDS:
            setattr(new_project, field, payload.get(field) or "")
        new_project.save()

        for node_id in ProjectFeatureSelection.objects.filter(
            project=source
        ).values_list("node_id", flat=True):
            ProjectFeatureSelection.objects.create(
                project=new_project, node_id=node_id
            )

        for node_id in ProjectFeatureExclusion.objects.filter(
            project=source
        ).values_list("node_id", flat=True):
            ProjectFeatureExclusion.objects.create(
                project=new_project, node_id=node_id
            )

        for override in ProjectFeatureContentOverride.objects.filter(project=source):
            ProjectFeatureContentOverride.objects.create(
                project=new_project,
                node_id=override.node_id,
                document_type=override.document_type,
                body=override.body,
                bullets=override.bullets,
            )

        custom_section_id_map: dict[int, int] = {}
        for section in ProjectCustomSection.objects.filter(project=source):
            new_section = ProjectCustomSection.objects.create(
                project=new_project,
                title=section.title,
                content=section.content,
                order=section.order,
            )
            custom_section_id_map[section.id] = new_section.id

        for setting in ProjectPreviewSetting.objects.filter(project=source):
            ProjectPreviewSetting.objects.create(
                project=new_project,
                document_type=setting.document_type,
                section_order=_remap_section_keys(
                    setting.section_order, custom_section_id_map
                ),
                excluded_sections=_remap_section_keys(
                    setting.excluded_sections, custom_section_id_map
                ),
                screenshot_selections={},
                caption_overrides={},
            )

    return JsonResponse(_serialize(new_project), status=201)
