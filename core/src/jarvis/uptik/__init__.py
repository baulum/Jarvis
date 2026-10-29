"""UpTik connector: publish short videos on TikTok, Instagram Reels and (optionally) YouTube Shorts by driving the
platforms' own web upload pages in a real Chrome – without developer apps, for accounts Sir logged into himself.

Concept after UpTik (github.com/tymon5368/uptik, MIT, © 2026 Tymon): platform uploader ports, persistent outbox
queue with crash recovery, selector fallback lists, a circuit breaker on platform roadblocks. No UpTik code is used
or run; see THIRD_PARTY_NOTICE.md and docs/UPTIK_EVALUATION.md. Deliberately *not* adopted: removing captcha
frames, hiding the automation flag, auto-accepting dialogs.

Layout:

* ``model``       – platforms, post states, error classes (retryable / permanent / manual review)
* ``validation``  – per-platform rules (caption, hashtags, title, file type, size, duration) and video probing
* ``timing``      – publish time in Europe/Berlin, exponential backoff
* ``circuit``     – roadblock detection (captcha, checkpoint, 2FA, rate limit, HTTP 429, unusual activity)
* ``sessions``    – browser sessions (cookies + local storage) AES-GCM encrypted, key in the OS secret store
* ``store``       – own SQLite database: accounts, queue, history, inbox (survives restarts)
* ``runners``     – where the browser runs: on the Core host or on Sir's computer via the Device Agent
* ``adapters``    – one adapter per platform, written against the small ``BrowserSession`` interface
* ``service``     – accounts, login flow, queue worker, fan-out, retries, circuit breaker, video hand-off
* ``tools`` / ``api`` – agent tools (through the approval system) and the REST API for desktop and phone
"""
