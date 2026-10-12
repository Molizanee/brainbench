"""Job handler modules. Each section adds one eager import line here so its handler registers.

Handler modules keep their heavy dependencies behind ``lazy import``; importing them here is
cheap and only performs the ``@register`` side effect.
"""

from app.jobs import figure as figure
from app.jobs import ingest as ingest
from app.jobs import noop as noop
from app.jobs import video as video
