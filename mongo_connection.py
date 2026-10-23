from datetime import timezone

from django.conf import settings
from pymongo import MongoClient

_CLIENTS = {}


def get_db_connection_machine(plant_code):

    if plant_code == "002":
        uri = settings.COE_CONNECTION
        db_name = "Testdb"

    elif plant_code == "034":
        uri = settings.PGR_CONNECTION
        db_name = "pgr_trace"

    elif plant_code == "143":
        uri = settings.VLM_CONNECTION
        db_name = "trace"

    elif plant_code == "003":
        uri = settings.ANNUR_CONNECTION
        db_name = "LGB_DB"

    elif plant_code == "004":
        uri = settings.VYM_CONNECTION
        db_name = "factory_db"

    elif plant_code == "123":
        uri = settings.PPM_CONNECTION
        db_name = "trace"

    elif plant_code == "120":
        uri = settings.PMD2_CONNECTION
        db_name = "trace"

    elif plant_code == "041":
        username = settings.MONGO_USERNAME
        password = settings.MONGO_PASSWORD
        auth_db = settings.MONGO_AUTH_DB
        host = settings.MONGO_HOST

        uri = (
            f"mongodb://{username}:{password}"
            f"@{host}:27017/trace?authSource={auth_db}"
        )

        db_name = "trace"

    else:
        raise ValueError(f"Unsupported plant code: {plant_code}")

    # Create MongoClient only once
    if uri not in _CLIENTS:
        _CLIENTS[uri] = MongoClient(
            uri,
            serverSelectionTimeoutMS=15000,
            tz_aware=True,
            tzinfo=timezone.utc,
        )

    # Reuse existing MongoClient
    client = _CLIENTS[uri]

    return client[db_name]
