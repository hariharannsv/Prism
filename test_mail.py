"""Package/module `core.test_mail`."""

from datetime import datetime

from core.mongo_connection import get_db_connection_machine


def SaveData():
    """Insert a sample document into MongoDB (manual/test helper)."""
    db = get_db_connection_machine("041")
    collection = db["test_data"]
    collection.insert_one({"message": "Manual insert", "timestamp": datetime.now()})
