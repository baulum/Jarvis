"""Social Media: the daily promo video per company profile (PJE Systems, Apo HR …; ``companies.py``).

Pipeline (playbook ``social_daily_video``, started by each company's schedule "<Name> Daily Social Video"): concept
(``concept.py``, Claude + anti-repetition rules in ``variety.py``) → storyboard (``storyboard.py``, JSON schema)
→ render (``render.py``: Remotion project ``social/`` + FFmpeg sound design ``sfx.py``) → quality control
(``qa.py``, ``media.py``) → captions (``captions.py``) → library (``repo.py``) → e-mail (``emailer.py``) →
optional publishing (``platforms.py``). ``service.py`` ties it together, ``tools.py`` holds the connector and its
tools, ``api.py`` the REST API for the desktop and mobile apps. Details: docs/SOCIAL_MEDIA.md.
"""
