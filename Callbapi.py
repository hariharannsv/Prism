"""Service module to call SAP BAPI functions via pyrfc with token-based authentication."""

import json
import traceback
from decimal import Decimal

import requests
from django.conf import settings

# PyRFC is an optional dependency: it is only required when a SAP RFC call is
# actually executed.  Importing it at module level would break Django startup
# (core/urls.py imports the views that use this module) on every machine where
# PyRFC / the SAP RFC SDK is not installed, so it is loaded on first use.
PYRFC_UNAVAILABLE_MESSAGE = (
    "SAP integration is unavailable: the 'pyrfc' package is not installed. "
    "Install PyRFC and the SAP RFC SDK to enable SAP BAPI calls."
)


class PyrfcUnavailableError(RuntimeError):
    """Raised when a SAP RFC call is requested but PyRFC is not installed."""


def get_pyrfc_connection():
    """Return the ``pyrfc.Connection`` class, importing pyrfc on first use.

    Raises :class:`PyrfcUnavailableError` when PyRFC is not installed so the
    caller can report a controlled error instead of failing at import time.
    """
    try:
        from pyrfc import Connection
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise PyrfcUnavailableError(PYRFC_UNAVAILABLE_MESSAGE) from exc
    return Connection


def call_bapi(bapi, params):
    """Call a BAPI function using dynamic SAP connection parameters."""
    try:
        # Fail fast (and without a pointless token round trip) when the
        # optional SAP RFC dependency is missing.
        Connection = get_pyrfc_connection()

        # print(f"Calling BAPI: {bapi} with params: {params}")
        bapi_api = settings.BAPI_API

        # Get token
        token_response = requests.post(
            bapi_api, json={"apitype": "accesstoken"}, timeout=10
        )
        token_response.raise_for_status()
        token = token_response.json().get("token")
        headers = {"Authorization": f"Bearer {token}"}

        # Get login info
        login_response = requests.post(
            bapi_api,
            headers=headers,
            json={"apitype": "login", "client": "RQS"},
            timeout=10,
        )
        login_response.raise_for_status()
        response = login_response.json()

        # Extract connection parameters
        conn_params = {
            "ashost": response["ip"],
            "sysnr": response["sysnr"],
            "client": response["client"],
            "user": response["user_name"],
            "passwd": response["password"],
        }

        # Connect and call BAPI
        conn = Connection(**conn_params)
        result = conn.call(bapi, **params)

        # Handle Decimal serialization
        def decimal_to_str(obj):
            """Convert Decimal objects to strings for JSON serialization."""
            if isinstance(obj, Decimal):
                return str(obj)
            raise TypeError("Object of type Decimal expected")

        json_data = json.dumps(result, indent=4, default=decimal_to_str)
        # print(json_data, "json dataa")
        return {"data": json.loads(json_data), "status": True}

    except requests.RequestException as req_err:
        return {"data": f"Request failed: {req_err}", "status": False}
    except PyrfcUnavailableError as exc:
        return {"data": str(exc), "status": False}
    except Exception:

        return {"data": f"Unhandled error: {traceback.format_exc()}", "status": False}


def call_bapi_api(bapi, params, client):
    """Call a BAPI function using dynamic SAP connection parameters."""
    try:
        # Fail fast (and without a pointless token round trip) when the
        # optional SAP RFC dependency is missing.
        Connection = get_pyrfc_connection()

        bapi_api = settings.BAPI_API

        # Get token
        token_response = requests.post(
            bapi_api, json={"apitype": "accesstoken"}, timeout=10
        )
        token_response.raise_for_status()
        token = token_response.json().get("token")
        headers = {"Authorization": f"Bearer {token}"}

        # Get login info
        login_response = requests.post(
            bapi_api,
            headers=headers,
            json={"apitype": "login", "client": client},
            timeout=10,
        )
        login_response.raise_for_status()
        response = login_response.json()

        # Extract connection parameters
        conn_params = {
            "ashost": response["ip"],
            "sysnr": response["sysnr"],
            "client": response["client"],
            "user": response["user_name"],
            "passwd": response["password"],
        }

        # Connect and call BAPI
        conn = Connection(**conn_params)
        result = conn.call(bapi, **params)

        # Handle Decimal serialization
        def decimal_to_str(obj):
            """Convert Decimal objects to strings for JSON serialization."""
            if isinstance(obj, Decimal):
                return str(obj)
            raise TypeError("Object of type Decimal expected")

        json_data = json.dumps(result, indent=4, default=decimal_to_str)
        return {"data": json.loads(json_data), "status": True}

    except requests.RequestException as req_err:
        return {"data": f"Request failed: {req_err}", "status": False}
    except PyrfcUnavailableError as exc:
        return {"data": str(exc), "status": False}
    except Exception:
        return {"data": f"Unhandled error: {traceback.format_exc()}", "status": False}
