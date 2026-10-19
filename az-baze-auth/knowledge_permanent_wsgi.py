"""Opt-in WSGI entry point; existing server.py and all other modules stay intact.

Use only after publication and configuration of the permanent storage root.
No preparation or conversion is performed by importing this entry point.
"""
import os

if not os.environ.get('AZBAZE_KNOWLEDGE_PERMANENT_ROOT', '').strip():
    raise RuntimeError('Permanent storage root must be configured before activation')

from server import app
import knowledge_permanent

knowledge_permanent.install(app)
