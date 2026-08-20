import json

from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


def _parse_json(request):
    """Parse a JSON request body, returning (payload, error_response)."""
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError:
        return None, JsonResponse({"detail": "無效的 JSON"}, status=400)


# NOTE: these endpoints are exempted from CSRF checks because T2 ships an
# API-only surface with no HTML form/template yet. Once a real frontend is
# built (later ticket), it should either issue/send CSRF tokens or this
# should move to a token-based auth scheme — see docs/adr/0001 for the
# "avoid premature environment coupling" precedent set in T1.
@csrf_exempt
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


@csrf_exempt
@require_http_methods(["POST"])
def logout_view(request):
    """Clears the current session, if any."""
    logout(request)
    return JsonResponse({"detail": "已登出"})
