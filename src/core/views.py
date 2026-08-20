from django.db import connection
from django.http import JsonResponse


def health_check(request):
    """Liveness/readiness endpoint for the skeleton.

    Also proves the database connection works by round-tripping a trivial
    query, independent of any application model.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()

    return JsonResponse({"status": "ok"})
