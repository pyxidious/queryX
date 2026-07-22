(() => {
  let busy = false;

  function setBusy(workspace, active, message = "Scansione in corso…") {
    busy = active;
    workspace.setAttribute("aria-busy", String(active));
    const loader = workspace.querySelector("#source-scan-loading");
    if (loader) {
      loader.hidden = !active;
      const label = loader.querySelector("[data-loading-message]");
      if (label) label.textContent = message;
    }
    workspace.querySelectorAll("button[type='submit']").forEach((button) => {
      button.disabled = active;
      button.setAttribute("aria-disabled", String(active));
    });
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-source-scan-form]");
    if (!form) return;
    event.preventDefault();
    if (busy) return;
    const workspace = document.getElementById("source-workspace");
    if (!workspace) return;
    setBusy(workspace, true);
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: { Accept: "text/html" },
      });
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const replacement = parsed.getElementById("source-workspace");
      if (!replacement) throw new Error("Source response is missing its workspace");
      busy = false;
      workspace.replaceWith(replacement);
    } catch (_) {
      setBusy(workspace, false);
      const error = workspace.querySelector("#source-scan-client-error");
      if (error) error.hidden = false;
    }
  });
})();
