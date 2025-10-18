import os
import json
import requests
from requests.auth import HTTPBasicAuth
from pipeline.config import get_key, INTEGRATOR_KEY

BASE_URL = "https://api.pos.dutchie.com"

def fetch_sales_json(store: str, start_date=None, end_date=None):
    """
    Fetch data from Dutchie API if DUTCHIE_INTEGRATOR_KEY is set,
    otherwise fall back to local mock files (previously uploaded JSON/CSV).
    """

    # if no integrator key -> fallback mode
    if not INTEGRATOR_KEY or INTEGRATOR_KEY.strip() == "" or INTEGRATOR_KEY.startswith("mock"):
        print(f"[OFFLINE MODE] No integrator key found. Using local data for {store}.")
        local_path = f"data/{store.lower()}_transactions.json"
        if os.path.exists(local_path):
            with open(local_path, "r") as f:
                return json.load(f)
        else:
            raise FileNotFoundError(f"No local data file found for {store}. Expected: {local_path}")

    # --- Real API mode ---
    endpoint = f"{BASE_URL}/reporting/register-transactions"

    api_key = get_key(store)
    auth = HTTPBasicAuth(api_key, INTEGRATOR_KEY)

    params = {}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    print(f"[API FETCH] Fetching data for {store} from {endpoint}")
    response = requests.get(endpoint, auth=auth, params=params, timeout=60)

    if response.status_code != 200:
        raise RuntimeError(
            f"API request failed for {store}: {response.status_code} - {response.text[:200]}"
        )

    data = response.json()
    return data


def save_json(data, store):
    """Save data fetched from API (or fallback mock) into /data folder."""
    os.makedirs("data", exist_ok=True)
    file_path = f"data/{store.lower()}_transactions.json"
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)
    return file_path
