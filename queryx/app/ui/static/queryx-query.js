(() => {
  let busy = false;
  let lastSubmitter = null;

  function setBusy(active, message = "Operazione in corso…") {
    const workspace = document.getElementById("query-workspace");
    const loader = document.getElementById("query-loading");
    if (!workspace || !loader) return;
    busy = active;
    workspace.setAttribute("aria-busy", String(active));
    loader.hidden = !active;
    const label = loader.querySelector("[data-loading-message]");
    if (label) label.textContent = message;
    workspace.querySelectorAll("button[type='submit']").forEach((button) => {
      button.disabled = active;
      button.setAttribute("aria-disabled", String(active));
    });
  }

  async function submit(event) {
    const form = event.target.closest("form[data-query-form]");
    if (!form) return;
    event.preventDefault();
    if (busy) return;
    const submitter = event.submitter || (
      lastSubmitter?.form === form ? lastSubmitter : null
    );
    lastSubmitter = null;
    const message = submitter?.dataset.loadingText || "Operazione in corso…";
    setBusy(true, message);
    const data = new FormData(form);
    if (submitter?.name) data.set(submitter.name, submitter.value);
    const action = submitter?.getAttribute("formaction") || form.getAttribute("action");
    try {
      const response = await fetch(action, {
        method: "POST",
        body: data,
        credentials: "same-origin",
        headers: { Accept: "text/html" },
      });
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const replacement = parsed.getElementById("query-workspace");
      const workspace = document.getElementById("query-workspace");
      if (!replacement || !workspace) throw new Error("Query response is missing its workspace");
      setBusy(false);
      workspace.replaceWith(replacement);
    } catch (_) {
      setBusy(false);
      const loader = document.getElementById("query-loading");
      const label = loader?.querySelector("[data-loading-message]");
      if (label) label.textContent = "Operazione non completata. Riprova.";
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("button[type='submit']");
    if (button?.form?.matches("form[data-query-form]")) lastSubmitter = button;
  });
  document.addEventListener("submit", submit);
})();
