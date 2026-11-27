/** Keep AI Insight entity-row labels readable next to date/select controls. */
const DASHBOARD = "imou-zhijian";
const STYLE_ID = "imou-insight-form-fix";
const CONTROL_TAGS = new Set([
  "ha-date-input",
  "ha-time-input",
  "ha-select",
  "ha-combo-box",
  "ha-control-select-menu",
  "ha-picker-field",
]);

const ROW_CSS = `
.info {
  flex: 1 1 4.5rem !important;
  min-width: 4.5rem !important;
  max-width: 42%;
}
.flex {
  flex: 1 1 9rem !important;
  min-width: 0 !important;
  max-width: 100%;
  overflow: hidden;
}
.flex ::slotted(*) {
  min-width: 0 !important;
  max-width: 100% !important;
  width: 100%;
  box-sizing: border-box;
}
@media (max-width: 480px) {
  .row {
    flex-wrap: wrap;
    align-items: stretch;
  }
  .info {
    flex: 1 1 100% !important;
    max-width: none !important;
    padding-inline-end: 16px;
    padding-bottom: 2px;
  }
  .flex {
    flex: 1 1 100% !important;
    margin-inline-start: 40px;
  }
  .flex ::slotted(*) {
    margin-inline-start: 0 !important;
  }
}
`;

const CONTROL_CSS = `
:host {
  display: block;
  min-width: 0 !important;
  max-width: 100%;
  width: 100%;
  box-sizing: border-box;
}
ha-input,
wa-input,
ha-select,
ha-combo-box,
.field {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
}
`;

function onInsightDashboard() {
  return window.location.pathname.includes(`/${DASHBOARD}`);
}

function inject(root, css) {
  if (!root || root.getElementById?.(STYLE_ID)) {
    return;
  }
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = css;
  root.appendChild(style);
}

function walk(root) {
  if (!root?.querySelectorAll) {
    return;
  }
  root.querySelectorAll("*").forEach((el) => {
    if (el.localName === "hui-generic-entity-row" && el.shadowRoot) {
      inject(el.shadowRoot, ROW_CSS);
    }
    if (CONTROL_TAGS.has(el.localName) && el.shadowRoot) {
      inject(el.shadowRoot, CONTROL_CSS);
    }
    if (el.shadowRoot) {
      walk(el.shadowRoot);
    }
  });
}

let scanTimer = 0;
function scheduleScan() {
  window.clearTimeout(scanTimer);
  scanTimer = window.setTimeout(() => {
    if (onInsightDashboard()) {
      walk(document);
    }
  }, 120);
}

if (!window.__imouInsightFormBound) {
  window.__imouInsightFormBound = true;
  scheduleScan();
  new MutationObserver(scheduleScan).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  window.addEventListener("location-changed", scheduleScan);
  window.addEventListener("popstate", scheduleScan);
}
