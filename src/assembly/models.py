import re

from django.db import models

from features.models import (
    DOCUMENT_TYPE_CHOICES,
    DOCUMENT_TYPE_CODES,
    PROSE_DOCUMENT_TYPES,
    FeatureNode,
)
from overrides.models import ProjectCustomSection, get_effective_content
from projects.models import PROJECT_VARIABLE_FIELDS, Project
from screenshots.models import (
    ProjectScreenshot,
    ScreenshotRequirement,
    resolve_applicable_screenshot,
)
from selections.models import compute_effective_selection

# T7 scope (Issue #8): 文件組裝引擎＋文件預覽頁.
#
# 組裝公式（見 Issue #1 Document Assembly Rules / Issue #8 What to build）：
#   範本骨架（每種文件類型固定一份，本 app 不建模「骨架」本身——骨架＝
#   「這個文件類型有哪些已勾選節點對應的章節＋自訂章節」這件事本身，由
#   assemble_document() 在查詢當下即時運算出來）
#     + 專案變數（substitute_project_variables，讀 projects.Project 欄位）
#     + （勾選節點的）功能節點內容／覆寫（reuse overrides.get_effective_content，
#       其內部已經處理「有覆寫用覆寫，沒有用共用預設」）
#     + 專案自訂章節（overrides.ProjectCustomSection）
#     + 客戶截圖（reuse screenshots.resolve_applicable_screenshot）
#     + 預覽調整（本 app 的 ProjectPreviewSetting：排序／是否包含／換圖／
#       改圖說，且都持久化於 (project, document_type) 這個 key 下）
#
# 刻意不修改 selections/overrides/screenshots/features/projects 任何檔案，
# 只讀取重用既有的 compute_effective_selection() / get_effective_content() /
# resolve_applicable_screenshot()，避免重新實作或分裂出第二套判斷邏輯。

DOCUMENT_TYPE_LABELS = dict(DOCUMENT_TYPE_CHOICES)

# 章節 key 的兩種形狀：一個已勾選 FeatureNode 對應的章節，或一個專案自訂
# 章節。用簡單的字串前綴（而非另開一張「章節」表）表示，因為章節本身沒有
# 獨立的身分——它永遠是「某個節點」或「某個自訂章節」在某個文件類型下的
# 投影，是 assemble_document() 每次查詢當下才組出來的東西。
SECTION_KEY_NODE_PREFIX = "node:"
SECTION_KEY_CUSTOM_PREFIX = "custom:"

SECTION_KEY_RE = re.compile(r"^(?:node|custom):\d+$")


def node_section_key(node_id: int) -> str:
    return f"{SECTION_KEY_NODE_PREFIX}{node_id}"


def custom_section_key(section_id: int) -> str:
    return f"{SECTION_KEY_CUSTOM_PREFIX}{section_id}"


class ProjectPreviewSetting(models.Model):
    """一個專案針對某文件類型的「文件預覽頁」持久化調整（Issue #1 決策6／
    Issue #8：調整結果會被持久化保存，不是只在這次瀏覽期間有效）。

    設計判斷（按 Issue #8 要求在 PR 說明中誠實標註的一項）：這裡刻意以
    (project, document_type) 為鍵值，而不是單一個 project 一份設定。理由：
    - Issue #8 acceptance criteria 明講「各文件類型的組裝各自獨立」；
    - 章節順序／是否包含，天然是「這個文件類型的章節結構」的調整——系統
      測試文件的章節順序沒有理由要跟教育訓練簡報的投影片順序綁在一起；
    - 若共用同一份設定，四種文件類型的預覽調整會互相污染，直接違反
      「各文件類型的組裝各自獨立」這條驗收標準。
    Issue #1 決策6原文只講「專案層級」，沒有明講是否要再依文件類型切分；
    這裡採用「每個文件類型各自一份」的更細粒度解讀。

    四個持久化欄位：
    - `section_order`：這個文件類型的章節排序（章節 key 陣列）。未出現在
      這份清單裡的章節（例如使用者尚未動過排序時，或新勾選的節點／新增
      的自訂章節）一律安插在清單最後，順序採預設順序（見
      `assemble_document()`）——不會因為排序設定「過期」就消失不見。
    - `excluded_sections`：本文件類型預覽層級「不列入」的章節 key 陣列。
      **這是疊加在 T4 選擇層（勾選＋排除清單）之上、獨立於 T4 的第二層
      機制**，只影響「這個文件類型的預覽／產出要不要顯示這個章節」，不會
      反過來影響 T4 的 `ProjectFeatureSelection`／`ProjectFeatureExclusion`
      本身、不影響其他文件類型的組裝、也不影響 T5 截圖檢查清單（那份清單
      仍然只看 T4 的 `compute_effective_selection()`）。這是本票最大的設計
      判斷之一，理由見下方模組說明。
    - `screenshot_selections`：`{截圖需求 id(字串): ProjectScreenshot id}`
      ——手動指定這個文件類型的預覽要顯示哪一張圖，覆蓋
      `resolve_applicable_screenshot()` 的自動判斷結果（例如使用者想在
      這份文件裡改用另一張客製圖，但不想影響其他文件類型的自動判斷）。
    - `caption_overrides`：`{截圖需求 id(字串): 圖說文字}`——這個文件類型
      預覽層級的圖說覆蓋，優先於該張 `ProjectScreenshot.caption` 本身。

    未儲存的欄位一律視為「還沒有人調整過」，`assemble_document()` 退回預設
    行為（預設順序、全部包含、自動判斷截圖、使用截圖本身的圖說）。
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="preview_settings"
    )
    document_type = models.CharField(
        "文件類型", max_length=30, choices=DOCUMENT_TYPE_CHOICES
    )

    section_order = models.JSONField("章節排序", blank=True, default=list)
    excluded_sections = models.JSONField(
        "本文件類型預覽層級排除的章節", blank=True, default=list
    )
    screenshot_selections = models.JSONField(
        "本文件類型預覽層級手動指定的截圖", blank=True, default=dict
    )
    caption_overrides = models.JSONField(
        "本文件類型預覽層級的圖說覆蓋", blank=True, default=dict
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "document_type"],
                name="unique_project_document_type_preview_setting",
            )
        ]
        ordering = ["project_id", "document_type"]

    def __str__(self) -> str:
        return f"{self.project_id} preview / {self.document_type}"


# --- {{project_variables.xxx}} 佔位符替換 -----------------------------------

_PLACEHOLDER_RE = re.compile(r"\{\{\s*project_variables\.([^}]+?)\s*\}\}")


def _project_variable_lookup(project: Project) -> dict:
    """建立「佔位符 key -> 目前實際值（字串）」的對照表。

    設計判斷：Issue #8 acceptance criterion 1 給的範例是
    `{{project_variables.系統網址}}`（用中文欄位標籤），但 Issue #1 Document
    Assembly Rules 的一般敘述是 `{{project_variables.xxx}}`（沒有明講是英文
    欄位代碼還是中文標籤）。這裡刻意同時支援兩種 key：英文欄位代碼
    （例如 `system_url`，跟 `projects.PROJECT_VARIABLE_FIELDS` 一致）與該
    欄位的中文 `verbose_name`（例如 `系統網址`，跟 Project model 定義一致），
    兩者對應同一個值，避免因為使用者/後台編輯者用了其中一種寫法就失效。
    """
    lookup: dict[str, str] = {}
    for field_name in PROJECT_VARIABLE_FIELDS:
        field = Project._meta.get_field(field_name)
        value = getattr(project, field_name)
        if field_name == "acceptance_date":
            str_value = value.isoformat() if value else ""
        else:
            str_value = value or ""
        lookup[field_name] = str_value
        lookup[str(field.verbose_name)] = str_value
    return lookup


def substitute_project_variables(text: str, project: Project) -> tuple[str, list[str]]:
    """把 `text` 中的 `{{project_variables.xxx}}` 佔位符替換成 `project`
    目前的實際欄位值，回傳 `(替換後文字, 缺漏的變數 key 清單)`。

    設計判斷：當某個佔位符引用的變數缺少值（欄位為空/None），或引用了一個
    不存在的變數名稱（打錯字），這裡刻意**保留原始的 `{{...}}` 字面文字不
    做替換**，而不是替換成空字串。理由：Issue #1 講「缺少對應變數值時視為
    缺漏，比照缺少截圖的警告機制處理」——截圖缺漏時畫面不會偷偷放一張空白
    圖片頂替、而是清楚看得出「這裡缺一張圖」；比照這個精神，缺漏的變數也
    應該在預覽內容中維持「看得出來這裡有一個未解析的佔位符」，而不是悄悄
    變成空白文字，才不會讓使用者誤以為這段文字本來就是空的。是否要替換成
    空字串屬於本票需要自行判斷的灰色地帶（Issue #1 沒有明講），已在 PR
    說明中誠實標註。
    """
    if not text:
        return text, []

    lookup = _project_variable_lookup(project)
    missing: list[str] = []

    def _replace(match: re.Match) -> str:
        key = match.group(1).strip()
        value = lookup.get(key)
        if value:
            return value
        missing.append(key)
        return match.group(0)

    substituted = _PLACEHOLDER_RE.sub(_replace, text)
    return substituted, missing


# --- 文件組裝 ----------------------------------------------------------------


def _default_section_order(effective_node_ids: list[int], custom_sections: list) -> list[str]:
    """預設章節順序：已勾選節點依 id 排序（跟
    `selections.compute_effective_selection()` 回傳順序一致，保持單一事實
    來源），接著是專案自訂章節（依 `ProjectCustomSection.order` / id 排序，
    跟該 model 自己的 `Meta.ordering` 一致）。使用者從未調整過排序時，這就
    是預覽頁看到的順序；調整過後，`ProjectPreviewSetting.section_order` 取
    代這份預設值。"""
    return [node_section_key(nid) for nid in effective_node_ids] + [
        custom_section_key(cs.id) for cs in custom_sections
    ]


def _resolve_section_order(setting: "ProjectPreviewSetting | None", default_order: list[str]) -> list[str]:
    if setting is None or not setting.section_order:
        return default_order

    valid = set(default_order)
    # 只保留目前仍然有效的章節 key（例如節點被取消勾選、或自訂章節被刪除
    # 後，殘留在舊排序設定裡的 key 就此被忽略，不會讓組裝結果出現「幽靈
    # 章節」）。
    ordered = [key for key in setting.section_order if key in valid]
    seen = set(ordered)
    # 排序設定儲存之後才新出現的章節（新勾選的節點、新增的自訂章節）依
    # 預設順序安插在最後，不會消失不見。
    ordered.extend(key for key in default_order if key not in seen)
    return ordered


def assemble_document(project: Project, document_type: str) -> dict:
    """組裝 `project` 在 `document_type` 底下的完整預覽結果。

    每次呼叫都即時運算（不落地快取）——「產生文件當下才做快照」是
    `GeneratedDocument`（T8）的責任，T7 只需要隨時反映「現在」的組裝結果。

    回傳的每個章節都帶 `included` 旗標（是否被本文件類型的預覽層級排除，
    見 `ProjectPreviewSetting.excluded_sections` 的說明），刻意**不**在這裡
    就把 `included=False` 的章節從清單中拿掉——文件預覽頁需要看到「目前被
    排除的章節」本身（才能提供「重新納入」的切換 UI），真正決定「這份文件
    最終要不要含這個章節」的是下游呼叫端（例如 `validate_generation_readiness`
    只統計 `included=True` 的章節）。
    """
    effective_ids = compute_effective_selection(project)
    nodes_by_id = {n.id: n for n in FeatureNode.objects.filter(id__in=effective_ids)}
    custom_sections = list(ProjectCustomSection.objects.filter(project=project))
    custom_by_id = {cs.id: cs for cs in custom_sections}

    requirements_by_node: dict[int, list[ScreenshotRequirement]] = {}
    for req in ScreenshotRequirement.objects.filter(
        node_id__in=effective_ids, is_enabled=True
    ).order_by("node_id", "order", "id"):
        requirements_by_node.setdefault(req.node_id, []).append(req)

    setting = ProjectPreviewSetting.objects.filter(
        project=project, document_type=document_type
    ).first()

    default_order = _default_section_order(effective_ids, custom_sections)
    ordered_keys = _resolve_section_order(setting, default_order)
    excluded = set(setting.excluded_sections) if setting else set()
    screenshot_selections = setting.screenshot_selections if setting else {}
    caption_overrides = setting.caption_overrides if setting else {}

    is_prose = document_type in PROSE_DOCUMENT_TYPES

    sections = []
    all_missing_variables: set[str] = set()

    for key in ordered_keys:
        if key.startswith(SECTION_KEY_NODE_PREFIX):
            node_id = int(key[len(SECTION_KEY_NODE_PREFIX):])
            node = nodes_by_id.get(node_id)
            if node is None:
                # 節點目前不在有效已選清單中（未勾選／被排除清單排除／已
                # 停用）——完全不出現在組裝結果中（Issue #8 acceptance
                # criteria 2、3）。
                continue

            content = get_effective_content(project, node, document_type)
            section_missing: set[str] = set()

            if is_prose:
                body_sub, missing = substitute_project_variables(content["body"], project)
                section_missing.update(missing)
                bullets_sub: list[str] = []
            else:
                body_sub = ""
                bullets_sub = []
                for item in content["bullets"]:
                    item_sub, missing = substitute_project_variables(item, project)
                    bullets_sub.append(item_sub)
                    section_missing.update(missing)

            screenshot_entries = []
            for req in requirements_by_node.get(node.id, []):
                override_shot_id = screenshot_selections.get(str(req.id))
                if override_shot_id is not None:
                    shot = ProjectScreenshot.objects.filter(
                        pk=override_shot_id, project=project, requirement=req
                    ).first()
                else:
                    shot = resolve_applicable_screenshot(project, req, document_type)

                if str(req.id) in caption_overrides:
                    caption_raw = caption_overrides[str(req.id)]
                else:
                    caption_raw = shot.caption if shot is not None else ""
                caption_sub, missing = substitute_project_variables(caption_raw, project)
                section_missing.update(missing)

                screenshot_entries.append(
                    {
                        "requirement": {"id": req.id, "name": req.name},
                        "applicable": shot is not None,
                        "screenshot": _serialize_screenshot_ref(shot),
                        "caption": caption_sub,
                    }
                )

            all_missing_variables.update(section_missing)
            sections.append(
                {
                    "key": key,
                    "type": "node",
                    "node": node.id,
                    "custom_section": None,
                    "title": node.name,
                    "content_source": content["source"],
                    "body": body_sub,
                    "bullets": bullets_sub,
                    "screenshots": screenshot_entries,
                    "included": key not in excluded,
                    "missing_variables": sorted(section_missing),
                }
            )

        else:
            section_id = int(key[len(SECTION_KEY_CUSTOM_PREFIX):])
            custom_section = custom_by_id.get(section_id)
            if custom_section is None:
                # 自訂章節已被刪除——同樣不出現在組裝結果中。
                continue

            content_sub, missing = substitute_project_variables(
                custom_section.content, project
            )
            all_missing_variables.update(missing)
            sections.append(
                {
                    "key": key,
                    "type": "custom",
                    "node": None,
                    "custom_section": custom_section.id,
                    "title": custom_section.title,
                    "content_source": "custom",
                    "body": content_sub,
                    "bullets": [],
                    "screenshots": [],
                    "included": key not in excluded,
                    "missing_variables": sorted(missing),
                }
            )

    return {
        "project": project.id,
        "document_type": document_type,
        "document_type_label": DOCUMENT_TYPE_LABELS[document_type],
        "sections": sections,
        "missing_variables": sorted(all_missing_variables),
    }


def _serialize_screenshot_ref(shot: "ProjectScreenshot | None") -> dict | None:
    if shot is None:
        return None
    return {
        "id": shot.id,
        "is_custom": shot.is_custom,
        "image_url": shot.image.url if shot.image else None,
        "original_filename": shot.original_filename,
    }


def validate_generation_readiness(project: Project, document_type: str) -> dict:
    """產生文件前的檢查：報告目前 `included=True`（實際會出現在這份文件裡）
    的章節中，缺少哪些必要截圖、哪些變數佔位符缺值（Issue #8 What to
    build：驗證/檢查端點，供未來 T8/T9 的「產生」動作在動手產生前先問過）。

    設計判斷：已被本文件類型預覽層級排除（`included=False`）的章節不列入
    這裡的檢查——既然這個章節本來就不會出現在這份文件裡，它缺不缺截圖／
    變數不該擋下其他章節的產生。
    """
    assembled = assemble_document(project, document_type)

    missing_screenshots = []
    missing_variables: set[str] = set()

    for section in assembled["sections"]:
        if not section["included"]:
            continue

        missing_variables.update(section["missing_variables"])

        for entry in section["screenshots"]:
            if not entry["applicable"]:
                missing_screenshots.append(
                    {
                        "node": section["node"],
                        "section_title": section["title"],
                        "requirement_id": entry["requirement"]["id"],
                        "requirement_name": entry["requirement"]["name"],
                    }
                )

    missing_variables_list = sorted(missing_variables)
    ready = not missing_screenshots and not missing_variables_list
    return {
        "project": project.id,
        "document_type": document_type,
        "ready": ready,
        "missing_screenshots": missing_screenshots,
        "missing_variables": missing_variables_list,
    }
