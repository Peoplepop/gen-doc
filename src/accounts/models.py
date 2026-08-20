from django.db import models

# T2 uses Django's built-in django.contrib.auth.models.User for
# authentication (帳密登入) rather than a custom User model — see
# ADR-0001 and Issue #1's Core Domain Model, which only requires a
# login account and an `owner_user_id` reference on Project, both of
# which the built-in User model already provides.
