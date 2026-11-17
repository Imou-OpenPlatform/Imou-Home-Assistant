const EVENT_TRIAL = "imou_life_ai_insight_trial";
const ROOT_ID = "imou-life-trial-dialog";

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatMessage(message) {
  return escapeHtml(message).replaceAll("\n", "<br>");
}

function showTrialDialog(title, message) {
  let root = document.getElementById(ROOT_ID);
  if (!root) {
    root = document.createElement("div");
    root.id = ROOT_ID;
    root.style.cssText =
      "position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.45);";
    root.addEventListener("click", (event) => {
      if (event.target === root) {
        root.remove();
      }
    });
    document.body.appendChild(root);
  }
  const card = document.createElement("div");
  card.style.cssText =
    "max-width:min(28rem,92vw);margin:1rem;background:var(--card-background-color,#fff);color:var(--primary-text-color,#212121);border-radius:var(--ha-card-border-radius,12px);padding:1.25rem 1.5rem;box-shadow:var(--ha-card-box-shadow,0 4px 20px rgba(0,0,0,.25));";
  card.innerHTML = `
    <h2 style="margin:0 0 .75rem;font-size:1.25rem;font-weight:500;">${escapeHtml(title)}</h2>
    <div style="margin:0 0 1.25rem;line-height:1.5;">${formatMessage(message)}</div>
    <div style="text-align:right;">
      <button type="button" style="cursor:pointer;padding:.5rem 1.25rem;">确定</button>
    </div>`;
  card.querySelector("button").addEventListener("click", () => root.remove());
  root.replaceChildren(card);
}

function whenHassReady(callback) {
  const root = document.querySelector("home-assistant");
  if (root?.hass?.connection) {
    callback(root.hass);
    return;
  }
  const observer = new MutationObserver(() => {
    const hass = document.querySelector("home-assistant")?.hass;
    if (hass?.connection) {
      observer.disconnect();
      callback(hass);
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

if (!window.__imouLifeTrialBound) {
  window.__imouLifeTrialBound = true;
  whenHassReady((hass) => {
    hass.connection.subscribeEvents((ev) => {
      const data = ev?.data ?? {};
      showTrialDialog(data.title ?? "AI智见试用", data.message ?? "");
    }, EVENT_TRIAL);
  });
}
