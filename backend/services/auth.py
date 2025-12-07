# backend/services/auth.py
import os
WS_AUTH_TOKEN = os.getenv("WS_AUTH_TOKEN")
def validate_ws_token(token: str) -> bool:
    if not WS_AUTH_TOKEN:
        return False
    return token == WS_AUTH_TOKEN