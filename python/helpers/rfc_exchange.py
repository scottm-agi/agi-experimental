from __future__ import annotations
import os
from python.helpers import runtime, crypto, dotenv_manager as dotenv

async def get_root_password():
    if runtime.is_dockerized():
        pswd = _get_root_password()
    else:
        priv = crypto._generate_private_key()
        pub = crypto._generate_public_key(priv)
        enc = await runtime.call_development_function(_provide_root_password, pub)
        pswd = crypto.decrypt_data(enc, priv)
    return pswd
    
def _provide_root_password(public_key_pem: str):
    pswd = _get_root_password()
    enc = crypto.encrypt_data(pswd, public_key_pem)
    return enc

def _get_root_password():
    # 1. Try persistent file first
    possible_paths = ["/agix/data/root_password", "/agix/data/root_password", "data/root_password"]
    for path in possible_paths:
        try:
            if os.path.isfile(path):
                with open(path, "r") as f:
                    pswd = f.read().strip()
                if pswd:
                    return pswd
        except (OSError, ValueError):
            pass
            
    # 2. Fallback to .env
    return dotenv.get_dotenv_value(dotenv.KEY_ROOT_PASSWORD) or ""
