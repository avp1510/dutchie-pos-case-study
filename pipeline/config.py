import os
from dotenv import load_dotenv

# Load .env file from project root
load_dotenv()

# Retrieve API keys safely
INTEGRATOR_KEY = os.getenv("DUTCHIE_INTEGRATOR_KEY", "")
COLUMBUS_KEY = os.getenv("DUTCHIE_API_KEY_COLUMBUS", "")
CINCINNATI_KEY = os.getenv("DUTCHIE_API_KEY_CINCINNATI", "")

def get_key(store: str):
    """Return the right API key for the given store name."""
    store = store.lower()
    if "columbus" in store:
        return COLUMBUS_KEY
    elif "cincinnati" in store:
        return CINCINNATI_KEY
    return None
