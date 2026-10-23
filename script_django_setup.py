"""Configure Django for standalone scripts (import for side effect)."""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "PMS.settings")
django.setup()
READY = True
