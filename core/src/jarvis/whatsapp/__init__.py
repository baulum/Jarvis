"""WhatsApp connector.

The WhatsApp protocol is spoken by a small Node process (``whatsapp-sidecar/``, Baileys) that the Core starts and
supervises; the two talk JSON lines over the child's stdio only (``sidecar.py``). Everything else lives here:
persistence (``repo.py``), file safety (``files.py``), understanding of received files (``media.py``), transfers
to and from device agents (``transfer.py``), contact resolution (``contacts.py``), suggestions (``suggestions.py``),
the service tying it together (``service.py``), the agent tools (``tools.py``), the connector (``connector.py``)
and the REST API for the desktop app (``api.py``).
"""
