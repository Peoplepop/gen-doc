import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from features.models import DOCUMENT_TYPE_CODES
from projects.models import Project
from screenshots.models import ProjectScreenshot, ScreenshotRequirement

from .models import (
    SECTION_KEY_RE,
    ProjectPreviewSetting,
    assemble_document,
    validate_generation_readiness,
)


def _require_auth(request):
    """Returns a 401 JsonResponse if the request isn't authenticated,
    otherwise None. 同 projects/features/selections/overrides/screenshots
    app 的既有慣例：未登入無法存取任何文件預覽／組裝 API。"""
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


def _check_document_type(document_type):
    if document_type not in DOCUMENT_TYPE_CODES:
        return JsonResponse({"detail": "不支援的文件類型"}, status=404)
    return None


def _clean_section_key_list(payload, field):
    """驗證 payload[field]（若有帶）是一組合法的章節 key 字串陣列
    （"node:<id>" 或 "custom:<id>"）。回傳 (cleaned_or_None, error)。
    這是「整組覆寫」語意（跟 selections app 的 checked/excluded PUT 慣例
    相同）——呼叫端每次都送出這個欄位當下完整的陣列。"""
    raw = payload.get(field)
    if not isinstance(raw, list):
        return None, JsonResponse({"detail": f"{field} 須為章節 key 陣列"}, status=400)
    cleaned = []
    for item in raw:
        if not isinstance(item, str) or not SECTION_KEY_RE.match(item):
            return None, JsonResponse(
                {"detail": f"{field} 格式錯誤，須為 'node:<id>' 或 'custom:<id>': {item!r}"},
                status=400,
            )
        cleaned.append(item)
    return cleaned, None


def _apply_screenshot_selection_updates(project, existing: dict, updates: dict):
    """把 payload 帶來的 screenshot_selections 逐鍵合併進既有設定（不是整
    份覆蓋）——讓「只改一張圖的手動指定」不需要重送整份設定。value 為
    null 代表清除這個 key 的手動指定（改回自動判斷）。"""
    result = dict(existing)
    for key, value in updates.items():
        if not isinstance(key, str) or not key.isdigit():
            return None, JsonResponse(
                {"detail": f"screenshot_selections 的 key 須為截圖需求 id: {key!r}"},
                status=400,
            )
        if value is None:
            result.pop(key, None)
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            return None, JsonResponse(
                {"detail": "screenshot_selections 的值須為截圖 id（整數）或 null"},
                status=400,
            )
        requirement_id = int(key)
        requirement = ScreenshotRequirement.objects.filter(pk=requirement_id).first()
        if requirement is None:
            return None, JsonResponse(
                {"detail": f"找不到截圖需求: {requirement_id}"}, status=400
            )
        shot_exists = ProjectScreenshot.objects.filter(
            pk=value, project=project, requirement=requirement
        ).exists()
        if not shot_exists:
            return None, JsonResponse(
                {
                    "detail": (
                        f"找不到符合的截圖: requirement={requirement_id}, "
                        f"screenshot={value}"
                    )
                },
                status=400,
            )
        result[key] = value
    return result, None


def _apply_caption_override_updates(existing: dict, updates: dict):
    """同上，但是逐鍵合併 caption_overrides。value 為 null 代表清除這個 key
    的圖說覆蓋（改回使用該張 ProjectScreenshot 自己的 caption）。"""
    result = dict(existing)
    for key, value in updates.items():
        if not isinstance(key, str) or not key.isdigit():
            return None, JsonResponse(
                {"detail": f"caption_overrides 的 key 須為截圖需求 id: {key!r}"},
                status=400,
            )
        if value is None:
            result.pop(key, None)
            continue
        if not isinstance(value, str):
            return None, JsonResponse(
                {"detail": "caption_overrides 的值須為文字或 null"}, status=400
            )
        result[key] = value
    return result, None


@require_http_methods(["GET"])
def project_preview_detail(request, pk, document_type):
    """回傳 (project, document_type) 目前的完整組裝預覽結果——章節結構、
    順序、文字（已替換佔位符）、截圖與圖說，且已套用本文件類型持久化的
    預覽調整（Issue #8 What to build：組裝/預覽端點）。"""
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    dt_error = _check_document_type(document_type)
    if dt_error:
        return dt_error

    return JsonResponse(assemble_document(project, document_type))


@require_http_methods(["GET"])
def project_preview_validate(request, pk, document_type):
    """產生文件前的驗證/檢查端點：回傳目前會擋下產生動作的缺漏項目（缺少
    必要截圖、缺少變數值），以及整體 `ready` 旗標（Issue #8 What to
    build：驗證/檢查端點，T8/T9 的「產生」動作會呼叫這個判斷要不要擋下）。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    dt_error = _check_document_type(document_type)
    if dt_error:
        return dt_error

    return JsonResponse(validate_generation_readiness(project, document_type))


@require_http_methods(["PUT"])
def project_preview_adjustments(request, pk, document_type):
    """持久化使用者在文件預覽頁做的調整：章節順序、是否包含某章節（本
    文件類型預覽層級，疊加在 T4 選擇層之上——見 assembly/models.py 的
    `ProjectPreviewSetting` 說明）、手動指定截圖版本、圖說覆蓋。

    每個欄位都是選填、逐一生效：payload 沒帶的欄位維持原本已儲存的值不
    動。`section_order`／`excluded_sections` 是整組覆寫語意；
    `screenshot_selections`／`caption_overrides` 是逐鍵合併語意（帶 null
    值代表清除該 key 的覆蓋）——見上面兩個 helper 的說明。

    回傳套用調整後、重新組裝出的完整預覽結果，讓呼叫端立即看到調整生效。
    """
    auth_error = _require_auth(request)
    if auth_error:
        return auth_error

    project, error = _get_project_or_404(pk)
    if error:
        return error

    dt_error = _check_document_type(document_type)
    if dt_error:
        return dt_error

    payload, error = _parse_json(request)
    if error:
        return error

    setting, _created = ProjectPreviewSetting.objects.get_or_create(
        project=project, document_type=document_type
    )

    if "section_order" in payload:
        cleaned, error = _clean_section_key_list(payload, "section_order")
        if error:
            return error
        setting.section_order = cleaned

    if "excluded_sections" in payload:
        cleaned, error = _clean_section_key_list(payload, "excluded_sections")
        if error:
            return error
        setting.excluded_sections = cleaned

    if "screenshot_selections" in payload:
        raw = payload.get("screenshot_selections")
        if not isinstance(raw, dict):
            return JsonResponse(
                {"detail": "screenshot_selections 須為物件"}, status=400
            )
        merged, error = _apply_screenshot_selection_updates(
            project, setting.screenshot_selections, raw
        )
        if error:
            return error
        setting.screenshot_selections = merged

    if "caption_overrides" in payload:
        raw = payload.get("caption_overrides")
        if not isinstance(raw, dict):
            return JsonResponse({"detail": "caption_overrides 須為物件"}, status=400)
        merged, error = _apply_caption_override_updates(
            setting.caption_overrides, raw
        )
        if error:
            return error
        setting.caption_overrides = merged

    setting.save()
    return JsonResponse(assemble_document(project, document_type))
