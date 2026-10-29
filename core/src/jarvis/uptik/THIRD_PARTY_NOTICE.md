# Third-party notice – UpTik

The `jarvis.uptik` connector follows concepts of **UpTik** (https://github.com/tymon5368/uptik), an Omnichannel
Video Auto-Scheduler licensed under the MIT License. No UpTik source code, binary or installer is included,
executed or downloaded by Jarvis. The following concepts were re-implemented independently in Python:

* a platform uploader port (`ID`, `LoginURL`, `CheckLogin`, `UploadVideo`) → `jarvis.uptik.adapters.base.PlatformAdapter`
* a persistent outbox queue whose in-flight jobs return to *pending* after a crash → `jarvis.uptik.store.UptikStore.recover`
* "selector fallback matrices" for upload inputs and buttons → `PlatformAdapter.first`
* a circuit breaker on platform roadblocks (captcha, checkpoint, phone verification, rate limit, HTTP 429)
  → `jarvis.uptik.circuit` (extended: per account, hard stop until Sir resumes it, no automatic retry)

Deliberately not adopted: removing captcha/verification frames from the page, the Chrome switch
`--disable-blink-features=AutomationControlled`, `--no-sandbox`, auto-accepting JavaScript dialogs, the installer
(`curl | bash`, clearing the macOS quarantine flag) and the self-updater.

```
MIT License

Copyright (c) 2026 Tymon

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
