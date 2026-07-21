---
name: Bug report
about: Report something broken in the Imou integration
title: "[Bug] "
labels: bug
---

## What happened?


## Steps to reproduce


## Expected behavior


## Environment

- **Region:** China / Overseas
- **Home Assistant version:**
- **Integration version:**
- **AppId:**
- **Device SN:**

## Logs

Please enable **debug** logging, reproduce the issue once, then paste the relevant log lines as **text** (not only screenshots).

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

Search logs for `pyimouapi` / `imou_life` (and for setup failures: `listDeviceDetails`). Prefer lines like `url: ... request body: ... response: ...` that show the API `code` / `msg`.

**Redact** `accessToken` / `token` and any secrets before posting. AppId, API host, and result `code`/`msg` can stay visible.

```text
Paste relevant debug logs here
```
