"""WSGI entry point for PythonAnywhere.

The Web tab's WSGI file imports ``application`` from here (see
``pythonanywhere_wsgi.py`` — copy that file's contents into the WSGI
file shown in the PythonAnywhere dashboard). Local runs use
``python3 index.py`` instead; this module is only for the hosted
worker.
"""
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from index import app as application  # noqa: E402,F401
