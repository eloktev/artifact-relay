(function () {
  "use strict";

  var status = document.querySelector("[data-share-status]");
  var token = window.location.hash.slice(1);
  history.replaceState(null, "", window.location.pathname);

  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
    status.textContent = "Ссылка недействительна или повреждена.";
    return;
  }

  fetch(window.location.pathname + "/redeem", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: token })
  }).then(function (response) {
    token = "";
    if (!response.ok) throw new Error("redeem failed");
    window.location.reload();
  }).catch(function () {
    token = "";
    status.textContent = "Ссылка недействительна, истекла или была отозвана.";
  });
})();
