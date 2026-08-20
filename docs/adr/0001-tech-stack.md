# ADR-0001: 技術棧選型 — Python / Django / SQLite（開發）＋ PostgreSQL（部署）

## 狀態

已接受（Accepted）— 2026-08-20

## 背景

Issue #1（MVP Spec）的 Open Question #1 明確留下技術棧未決定：語言、Web 框架、資料庫種類。
唯一已確定的架構原則是「單體式應用程式，避免非必要的微服務拆分」，理由是：

- 維護簡單、單一或少量內部使用者
- 可在一般雲端環境部署
- 易於備份
- 未來容易加入 AI 功能
- 未來容易加入瀏覽器自動化擷圖功能（Out of Scope，但需保留擴充空間）

另外幾個未寫進 Issue #1、但屬於本 spike 決策時必須納入考量的限制：

- 系統使用者（同時也是本專案的 product owner）目前正在學習 Python，且有 Kubernetes / Nutanix NKP / OpenShift（OCP）的實務背景。
- 本專案未來可能交接給另一位開發者，或交給 AI coding agent（如 Codex）接續開發——技術棧應主流、文件齊全，方便第二方快速上手，避免選用冷門或需要大量客製說明才能理解的技術。
- MVP Spec 的 Testing Decisions 已固定測試 seam：**只在 HTTP API 層測試**（呼叫端點 → 驗證回應與資料庫最終狀態），不對內部函式/類別寫獨立單元測試。技術棧的測試工具需要能自然支援這種「打 HTTP 請求斷言回應」的寫法。
- MVP 的七個主要頁面中，多數是表單與樹狀勾選 UI（專案編輯、功能選擇、截圖管理、文件預覽排序、內容管理後台），而非重度前端互動的 SPA 需求。
- 「內容模組管理後台」（功能節點樹維護、各文件類型內容維護、覆寫可視化清單）本質上是一個典型的 CRUD 後台。

## 決策

採用以下技術棧：

| 項目 | 選擇 |
| --- | --- |
| 語言 | Python 3.11+ |
| Web 框架 | Django 5.2（LTS） |
| 資料庫（本機開發／測試） | SQLite（Django 預設，零安裝） |
| 資料庫（雲端部署目標） | PostgreSQL |
| Migration 機制 | Django 內建 migrations（`manage.py makemigrations` / `migrate`） |
| 測試執行器 | Django 內建測試框架（`manage.py test` + `django.test.Client`），HTTP 層斷言 |
| CI | GitHub Actions（`.github/workflows/ci.yml`），push / PR 時自動跑 migrate + test |
| 程式碼佈局 | 單一 repo、單體式 Django 專案，原始碼置於 `src/` 下（`src/manage.py`、`src/gen_doc/`、`src/core/`） |

## 理由

1. **與「使用者正在學 Python」直接對齊**：選 Python 讓使用者未來能實際讀懂並參與維護程式碼，而不只是「委外交給 AI 寫完就看不懂」。
2. **Django 對這個 domain 是近乎免費的槓桿**：MVP 有大量表單、樹狀資料、後台 CRUD（內容模組管理後台）需求，Django 內建 ORM、內建 migration、內建 admin（可作為「內容模組管理後台」的起點或參考實作）、內建測試 Client，能用最少的自訂程式碼滿足 spec 的大部分頁面，符合「維護簡單」的第一優先順序。
3. **單體式架構的預設選擇**：Django 本身就是單體優先的框架（一個專案、多個 app、共用一個資料庫），不需要額外決策去避免微服務化，天然符合 Issue #1 已定案的架構原則。
4. **主流、文件齊全、易交接**：Django 是最主流的 Python Web 框架之一，文件與社群資源量極大，市面上的開發者與 AI coding agent（包含 Codex）都對 Django 有大量訓練資料與慣例可循，符合「未來可能交接給第二方或 AI agent」的要求，優於選用小眾框架。
5. **測試 seam 天然契合**：Django 的 `django.test.Client` 本來就是以「發 HTTP 請求、斷言回應與 DB 狀態」為核心的測試工具，不需要額外的測試框架或膠水程式碼，就能滿足 spec「只在 HTTP API 層測試」的決策，且不誘使開發者去寫內部函式的單元測試。
6. **部署彈性，貼合 K8s／OCP 背景**：Django 應用是標準的 WSGI 應用，可用 gunicorn/uwsgi 包裝後放進容器，部署到 Kubernetes、Nutanix NKP、OpenShift（OCP）都是成熟且有大量範例的路徑；也同樣能部署到一般 PaaS（例如 Render、Railway、單台 VM + systemd）。技術棧不綁定任何特定雲端平台。
7. **資料庫：本機用 SQLite、部署用 PostgreSQL**：SQLite 讓本機開發與 CI 測試零安裝、零外部相依，最大化「維護簡單」；PostgreSQL 是 Django 生態中最成熟的正式環境資料庫選擇，備份／還原方案成熟（`pg_dump`／`pg_restore`），符合「易於備份」的需求。Django 的 ORM 讓兩者之間的切換只是 `DATABASE` 設定值的差異，不影響應用程式碼——正式的環境變數化資料庫設定（例如 `DATABASE_URL`）留給部署相關的後續票處理，避免本張骨架票過早引入額外套件與環境耦合。
8. **對未來擴充的保留**：
   - **AI 功能**：Python 生態（`anthropic`、`openai` 等官方 SDK）與 Django 的整合沒有特殊障礙，未來可直接在既有 app 中新增呼叫邏輯。
   - **瀏覽器自動化擷圖**：Python 的 Playwright/Selenium 生態成熟，可作為未來獨立的背景任務或管理指令（`manage.py` custom command）加入，不需要更換技術棧。

## 考慮過的替代方案

- **FastAPI + SQLAlchemy + Alembic**：更輕量、對「純 API」場景更貼合，但本專案七個頁面多數是伺服器端表單／後台頁面而非純 API 消費端，FastAPI 本身不含 admin、不含表單／模板系統，等於要自己組裝一套等價於 Django 內建的能力，維護成本反而更高，不符合「維護簡單」的第一優先順序。予以否決。
- **Node.js（Express/NestJS）+ Prisma**：同樣主流、文件齊全，但與「使用者正在學 Python」的目標衝突，未來使用者較難親自參與維護。予以否決。
- **Go（net/http 或 Gin）+ sqlc/GORM**：部署產物單一執行檔、資源效率高，適合雲端部署與 K8s，但學習曲線與「內容管理後台」這類 CRUD 場景的框架支援都不如 Django 成熟，且與使用者的學習目標（Python）不符。予以否決。
- **Django 直接假設 PostgreSQL（本機也用）**：曾考慮讓本機開發也直接用 PostgreSQL 以貼近正式環境，但這會讓「跑起骨架」多一道安裝資料庫的門檻，與 T1「可在本機成功啟動」的驗收標準精神（低摩擦）衝突。SQLite 對 T1 骨架與後續 CRUD 為主的功能票已足夠，正式環境仍以 PostgreSQL 為目標，兩者切換成本低。

## 影響

- 後續所有票（T2+）都應在此 Django 專案骨架上開發，App 切分方式（例如 `projects`、`features`、`screenshots`、`documents`）留給對應功能票決定，本 ADR 不預先規定。
- 測試一律透過 `django.test.Client` 打 HTTP 端點驗證，不對內部函式/類別寫獨立單元測試，呼應 Issue #1 的 Testing Decisions。
- 正式環境的 PostgreSQL 連線設定、容器化（Dockerfile）、K8s/OCP 部署清單，本 ADR 只定調方向，具體實作留給後續的部署相關票，避免本張骨架票（T1）範圍蔓延。
