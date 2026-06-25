import sys
import os
print("DEBUG: prepare.py strictly starting...", flush=True)
from python.helpers import dotenv_manager as dotenv, runtime, settings
import string
import random
from python.helpers.print_style import PrintStyle


import sys
import os

PrintStyle.standard("Preparing environment...")
print(f"DEBUG: Python version: {sys.version}", flush=True)
print(f"DEBUG: PYTHONPATH: {os.environ.get('PYTHONPATH')}", flush=True)
print(f"DEBUG: CWD: {os.getcwd()}", flush=True)

try:
    print("DEBUG: Initializing runtime...", flush=True)
    runtime.initialize()
    print("DEBUG: Runtime initialized.", flush=True)

    print(f"DEBUG: Dockerized: {runtime.is_dockerized()}", flush=True)
    print(f"DEBUG: is_development: {runtime.is_development()}", flush=True)

    # generate random root password if not set (for SSH)
    root_pass = dotenv.get_dotenv_value(dotenv.KEY_ROOT_PASSWORD)
    if not root_pass:
        root_pass = "".join(random.choices(string.ascii_letters + string.digits, k=32))
        PrintStyle.standard("Changing root password...")
        print("DEBUG: Generating random root password...", flush=True)
    
    if runtime.is_dockerized():
        print("DEBUG: Setting root password...", flush=True)
        settings.set_root_password(root_pass)

    # generate random RFC password if not set
    rfc_pass = dotenv.get_dotenv_value(dotenv.KEY_RFC_PASSWORD)
    if not rfc_pass:
        rfc_pass = "".join(random.choices(string.ascii_letters + string.digits, k=32))
        PrintStyle.standard("Generating random RFC password...")
        print("DEBUG: Generating random RFC password...", flush=True)
    
    print("DEBUG: Setting RFC password...", flush=True)
    settings.set_rfc_password(rfc_pass)
    print("DEBUG: RFC password set.", flush=True)
    print("DEBUG: Environment configuration complete.", flush=True)

except Exception as e:
    PrintStyle.error(f"Error in prepare: {e}")
    print(f"DEBUG: Prepare failed with error: {e}", flush=True)
    import traceback
    traceback.print_exc()
    import sys
    sys.exit(1)
