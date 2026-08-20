import json

from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods


def _parse_json(request):
    """Parse a JSON request body, returning (payload, error_response)."""
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError:
        return None, JsonResponse({"detail": "無效的 JSON"}, status=400)


@ensure_csrf_cookie
@require_http_methods(["GET"])
def csrf_view(request):
    """Primes the csrftoken cookie for API clients (SPA/test clients that
    have no HTML form to read a hidden input from). Callers should read the
    `csrftoken` cookie after hitting this once, then send it back as the
    `X-CSRFToken` header on every POST/PUT/PATCH/DELETE."""
    return JsonResponse({"csrfToken": get_token(request)})


@require_http_methods(["POST"])
def login_view(request):
    """Username/password login (帳密登入). Establishes a Django session."""
    payload, error = _parse_json(request)
    if error:
        return error

    username = payload.get("username", "")
    password = payload.get("password", "")

    user = authenticate(request, username=username, password=password)
    if user is None:
        return JsonResponse({"detail": "帳號或密碼錯誤"}, status=401)

    login(request, user)
    return JsonResponse({"detail": "登入成功", "username": user.username})


@require_http_methods(["POST"])
def logout_view(request):
    """Clears the current session, if any."""
    logout(request)
    return JsonResponse({"detail": "已登出"})
