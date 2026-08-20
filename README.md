# gen-doc

專案驗收文件自動產生平台。

## 技術棧

Python 3.11+ / Django 5.2 LTS / SQLite（本機開發）／PostgreSQL（部署目標）。
決策理由見 [`docs/adr/0001-tech-stack.md`](docs/adr/0001-tech-stack.md)。

## 本機開發

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python src/manage.py migrate
python src/manage.py runserver
```

啟動後可呼叫健康檢查端點確認服務正常：

```bash
curl http://127.0.0.1:8000/api/health/
# {"status": "ok"}
```

## 執行測試

```bash
python src/manage.py test core
```

> 注意：`manage.py test` 的測試探索以執行時的工作目錄為 top-level dir，若省略 `core` 從 repo 根目錄執行會找到 0 支測試（但仍回報 `OK`），務必帶上 app label。

## 專案文件

- `.file/專案驗收文件自動產生平台需求書.docx` — 需求書
- Issue #1（GitHub）— MVP Spec
- `docs/adr/` — 架構決策紀錄（ADR）
