---
name: Question / help
about: Ask how to use the integration or get help (not a bug report)
title: "[Question] "
labels: question
---

## What do you need help with?


## What you tried


## Environment

- **Region:** China / Overseas
- **Home Assistant version:**
- **Integration version:**
- **AppId:**
- **Device SN:**

## Logs

If setup fails, entities go unavailable, or API quota errors appear, please enable **debug** logging and paste the relevant lines as **text**.

### Enable debug logging

**Option A — temporary (UI)**  
1. **Settings → System → Logs**  
2. Three-dot menu (⋮) → set log level / enable debug  
3. Set these to **debug**:
   - `custom_components.imou_life`
   - `pyimouapi`

**Option B — persistent (`configuration.yaml`)**  
Then restart Home Assistant:

```yaml
logger:
  default: info
  logs:
    custom_components.imou_life: debug
    pyimouapi: debug
```

### What to paste

Reproduce the issue once, then search logs for `pyimouapi` / `imou_life` (and for setup failures: `listDeviceDetails`). Prefer lines like:

`url: ... request body: ... response: ...`

that show the API `code` / `msg` (e.g. `OP1013`).

**Redact** `accessToken` / `token` and any secrets before posting. AppId, API host, and result `code`/`msg` can stay visible.

```text
Paste relevant debug logs here (optional if not needed)
```

**Tip:** For professional support you may also open a ticket:

- **China:** https://open.imou.com/support
- **Overseas:** https://www.imou.com/support/contact-us
