# Contents for the PythonAnywhere Web tab's WSGI file.
#
# In the dashboard (Web tab -> Code -> WSGI configuration file), replace
# the file's contents with the lines below, adjusting the username if
# the account name differs from tinglott. Then click Reload.
#
# The project lives in /home/<username>/luna-test-driver (git clone of
# tinglott/luna-test-driver, branch pythonanywhere). Env vars are set
# in the Web tab, not in this file — no secrets here, ever.

import os
import sys

sys.path.insert(0, '/home/tinglott/luna-test-driver')
os.chdir('/home/tinglott/luna-test-driver')

from index import app as application
