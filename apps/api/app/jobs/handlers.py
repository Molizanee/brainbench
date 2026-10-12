"""Import every job handler so its ``@register`` runs. Each section adds one line here.

Only ``app.worker`` (and the test conftest) imports this module. Keeping the imports out of
``app/jobs/__init__.py`` means that ``from app.jobs.errors import ...`` in a service does not
drag every handler, and through them every service, into the import of that one module.
"""

from app.jobs import curation as curation
from app.jobs import figure as figure
from app.jobs import ingest as ingest
from app.jobs import noop as noop
from app.jobs import video as video
