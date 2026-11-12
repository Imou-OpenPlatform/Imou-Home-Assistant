const EVENT_TRIAL = "imou_life_ai_insight_trial";

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
  const dialog = document.createElement("dialog");
  dialog.setAttribute("open", "");
  dialog.style.cssText =
    "max-width: min(28rem, 92vw); border: none; border-radius: var(--ha-card-border-radius, 12px); padding: 1.25rem 1.5rem; box-shadow: var(--ha-card-box-shadow, 0 4px 20px rgba(0,0,0,.25));";
  dialog.innerHTML = `
    <h2 style="margin:0 0 .75rem;font-size:1.25rem;font-weight:500;">${escapeHtml(title)}</h2>
    <div style="margin:0 0 1.25rem;line-height:1.5;white-space:normal;">${formatMessage(message)}</div>
    <form method="dialog" style="margin:0;text-align:right;">
      <button type="submit" style="cursor:pointer;padding:.5rem 1.25rem;">OK</button>
    </form>`;
  const backdrop = () => {
    document.body.style.overflow = "";
    dialog.remove();
  };
  dialog.addEventListener("close", backdrop);
  dialog.addEventListener("cancel", backdrop);
  document.body.appendChild(dialog);
  document.body.style.overflow = "hidden";
  dialog.showModal();
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

whenHassReady((hass) => {
  hass.connection.subscribeEvents((ev) => {
    const data = ev?.data ?? {};
    showTrialDialog(data.title ?? "AI Insight", data.message ?? "");
  }, EVENT_TRIAL);
});
