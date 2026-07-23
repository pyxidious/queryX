"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const workspace = document.getElementById("query-workspace");
  const composer = document.getElementById("query-composer");
  const loadingPanel = document.getElementById("query-loading");
  const loadingMessage = document.querySelector("[data-loading-message]");
  const questionInput = document.getElementById(
    "natural-language-question"
  );
  const characterCount = document.querySelector(
    "[data-character-count]"
  );

  const forms = document.querySelectorAll("[data-query-form]");
  const suggestions = document.querySelectorAll(
    "[data-query-suggestion]"
  );
  const statusMessages = document.querySelectorAll(
    "[data-query-status]"
  );

  let submissionStarted = false;

  /**
   * Aggiorna il contatore dei caratteri della domanda.
   */
  const updateCharacterCount = () => {
    if (!questionInput || !characterCount) {
      return;
    }

    const currentLength = questionInput.value.length;
    const maximumLength =
      Number(questionInput.getAttribute("maxlength")) || 2000;

    characterCount.textContent =
      `${currentLength} / ${maximumLength}`;
  };

  /**
   * Nasconde gli avvisi presenti tramite fade-out.
   *
   * Viene utilizzato quando parte una nuova operazione, così gli avvisi
   * relativi alla richiesta precedente non rimangono visibili.
   */
  const hideStatusMessages = () => {
    statusMessages.forEach((status) => {
      status.classList.add("is-leaving");

      window.setTimeout(() => {
        status.hidden = true;
      }, 220);
    });
  };

  /**
   * Sostituisce il composer con il pannello di caricamento.
   */
  const showLoadingPanel = (message) => {
    if (submissionStarted) {
      return;
    }

    submissionStarted = true;

    hideStatusMessages();

    if (workspace) {
      workspace.setAttribute("aria-busy", "true");
    }

    if (loadingMessage) {
      loadingMessage.textContent =
        message || "Operazione in corso…";
    }

    if (composer) {
      composer.classList.add("is-leaving");
    }

    window.setTimeout(() => {
      if (loadingPanel) {
        loadingPanel.setAttribute("aria-hidden", "false");
        loadingPanel.classList.add("is-visible");
      }
    }, 140);
  };

  /**
   * Ripristina il composer.
   *
   * Serve soprattutto quando il browser annulla l'invio o torna alla pagina
   * tramite la back-forward cache.
   */
  const hideLoadingPanel = () => {
    submissionStarted = false;

    if (workspace) {
      workspace.setAttribute("aria-busy", "false");
    }

    if (loadingPanel) {
      loadingPanel.classList.remove("is-visible");
      loadingPanel.setAttribute("aria-hidden", "true");
    }

    window.setTimeout(() => {
      if (composer) {
        composer.classList.remove("is-leaving");
      }
    }, 140);
  };

  /**
   * Gestisce l'invio dei form QueryX.
   */
  forms.forEach((form) => {
    form.addEventListener("submit", (event) => {
      const submitter = event.submitter;

      if (!form.checkValidity()) {
        return;
      }

      const message =
        submitter?.dataset.loadingText ||
        "Elaborazione della richiesta in corso…";

      showLoadingPanel(message);

      /*
       * Disabilita i pulsanti soltanto dopo che il browser ha raccolto
       * il valore del submitter. Un ritardo di zero millisecondi evita
       * di perdere il parametro name/value del pulsante selezionato.
       */
      window.setTimeout(() => {
        form
          .querySelectorAll(
            "button[type='submit'], input[type='submit']"
          )
          .forEach((button) => {
            button.disabled = true;
          });
      }, 0);
    });
  });

  /**
   * Inserisce nel campo una delle domande dimostrative.
   */
  suggestions.forEach((suggestion) => {
    suggestion.addEventListener("click", () => {
      if (!questionInput) {
        return;
      }

      questionInput.value =
        suggestion.dataset.querySuggestion || "";

      updateCharacterCount();
      questionInput.focus();

      questionInput.setSelectionRange(
        questionInput.value.length,
        questionInput.value.length
      );
    });
  });

  if (questionInput) {
    questionInput.addEventListener(
      "input",
      updateCharacterCount
    );

    /*
     * Ctrl+Invio oppure Cmd+Invio avvia "Genera ed esegui".
     */
    questionInput.addEventListener("keydown", (event) => {
      const executeShortcut =
        (event.ctrlKey || event.metaKey) &&
        event.key === "Enter";

      if (!executeShortcut) {
        return;
      }

      const form = questionInput.closest("form");

      const executeButton = form?.querySelector(
        "button[name='execute'][value='true']"
      );

      if (!form || !executeButton) {
        return;
      }

      event.preventDefault();

      form.requestSubmit(executeButton);
    });
  }

  /*
   * Se la pagina viene ripristinata dalla cache del browser,
   * il pannello di caricamento non deve rimanere visibile.
   */
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      hideLoadingPanel();
    }
  });

  updateCharacterCount();
});