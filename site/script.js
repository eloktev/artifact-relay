// Progressive enhancements only: the page remains complete without JavaScript.
(() => {
  const buttons = document.querySelectorAll("[data-copy]");
  const status = document.getElementById("copy-status");
  if (!navigator.clipboard) return;

  document.documentElement.classList.add("clipboard-ready");
  buttons.forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copy);
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.textContent.trim());
        const original = button.textContent;
        button.textContent = "Copied";
        if (status) status.textContent = button.dataset.copySuccess || "Commands copied";
        window.setTimeout(() => { button.textContent = original; }, 1600);
      } catch (_) {
        if (status) status.textContent = "Copy failed. Select the commands and copy them manually.";
        target.focus();
      }
    });
  });
})();
