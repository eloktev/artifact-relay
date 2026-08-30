(function () {
  "use strict";

  var input = document.querySelector("[data-share-url]");
  var button = document.querySelector("[data-copy-share]");
  var status = document.querySelector("[data-copy-status]");
  var root = document.querySelector("[data-share-created]");
  if (root && root.getAttribute("data-return-url")) {
    history.replaceState(null, "", root.getAttribute("data-return-url"));
  }
  if (!input || !button || !status) return;

  button.addEventListener("click", function () {
    var copied = navigator.clipboard && window.isSecureContext
      ? navigator.clipboard.writeText(input.value)
      : Promise.reject(new Error("clipboard unavailable"));

    copied.then(function () {
      status.textContent = "Ссылка скопирована.";
      button.textContent = "Скопировано";
    }).catch(function () {
      input.focus();
      input.select();
      status.textContent = "Скопируйте выделенную ссылку.";
    });
  });
})();
