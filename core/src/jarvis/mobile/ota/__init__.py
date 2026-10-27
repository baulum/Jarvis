"""OTA updates of the Android app (``apps/jarvis_mobile``).

Lands a change to the app on the base branch (a coding job, a push from elsewhere), the core builds a signed
release APK, checks it and hands it to the paired phones. Description for Sir: docs/MOBILE.md › App-Updates.

- ``changes``    which files belong to the app (no build for core, docs or tests only)
- ``versioning`` versionCode (strictly increasing, persisted) and versionName (semver + code + commit)
- ``signing``    the one release key: created once, keystore in a protected folder, password in the Keychain
- ``toolchain``  Flutter, Android SDK, JDK – detected, with a clear reason when something is missing
- ``builder``    checkout of the commit, ``flutter build apk``, verification with apksigner/aapt2
- ``releases``   manifest per release (SHA-256, size, signature fingerprint, changelog), retention, withdraw
- ``service``    change detection, one build at a time (debounced), distribution (push), phone reports
- ``api``        ``/api/mobile/app-update/*`` – served by the mobile gateway, also over Tailscale
"""
