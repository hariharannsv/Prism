"""Package/module `PMS.celery`."""

# project_root/celery.py
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "PMS.settings")

app = Celery("PMS")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
