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

### 登入 API（session-based，需要 CSRF token）

所有會改變狀態的端點（登入、登出、專案的建立/修改/刪除）都受 Django CSRF 保護，呼叫前需先向 `GET /api/auth/csrf/` 取得 token，並在後續請求帶上 `X-CSRFToken` header 與同一個 session cookie：

```bash
# 1. 建立一個測試帳號（互動式，僅需一次）
python src/manage.py createsuperuser

# 2. 取得 CSRF token 並登入（-c/-b 共用同一個 cookie jar）
curl -c cookies.txt http://127.0.0.1:8000/api/auth/csrf/
TOKEN=$(python -c "import http.cookiejar,sys; jar=http.cookiejar.MozillaCookieJar('cookies.txt'); jar.load(); print([c.value for c in jar if c.name=='csrftoken'][0])")

curl -b cookies.txt -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/login/ \
  -H "X-CSRFToken: $TOKEN" -H "Content-Type: application/json" \
  -d '{"username": "<你的帳號>", "password": "<你的密碼>"}'

# 3. 之後打專案 API 時繼續帶 -b cookies.txt（session）與 -H "X-CSRFToken: $TOKEN"
curl -b cookies.txt http://127.0.0.1:8000/api/projects/
```

## 執行測試

```bash
python src/manage.py test core accounts projects features selections screenshots
```

> 注意：`manage.py test` 的測試探索以執行時的工作目錄為 top-level dir，若省略 app label 從 repo 根目錄執行會找到 0 支測試（但仍回報 `OK`），務必列出所有 app。

## 專案文件

- `.file/專案驗收文件自動產生平台需求書.docx` — 需求書
- Issue #1（GitHub）— MVP Spec
- `docs/adr/` — 架構決策紀錄（ADR）
