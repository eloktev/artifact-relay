/* Viewer behaviour. No network access of any kind: everything here is local DOM work. */
(function () {
  "use strict";

  var root = document.documentElement;

  /* Mermaid: only initialised when the page actually contains a diagram. */
  if (window.mermaid && document.querySelector(".mermaid")) {
    var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    window.mermaid.initialize({
      startOnLoad: true,
      securityLevel: "strict",
      theme: dark ? "dark" : "neutral",
      fontFamily: "inherit"
    });
  }

  /* Print / save as PDF. */
  var printButton = document.querySelector("[data-action='print']");
  if (printButton) {
    printButton.addEventListener("click", function () {
      window.print();
    });
  }

  /* Collapsible table of contents on narrow screens. */
  var toc = document.querySelector("[data-toc]");
  var tocToggle = document.querySelector("[data-action='toggle-toc']");
  if (toc && tocToggle) {
    tocToggle.addEventListener("click", function () {
      var open = toc.hasAttribute("hidden");
      if (open) {
        toc.removeAttribute("hidden");
      } else {
        toc.setAttribute("hidden", "");
      }
      tocToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  /* Highlight the section currently in view. */
  var links = Array.prototype.slice.call(document.querySelectorAll("[data-toc] a"));
  if (links.length && "IntersectionObserver" in window) {
    var byId = {};
    links.forEach(function (link) {
      byId[decodeURIComponent(link.hash.slice(1))] = link;
    });
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          var link = byId[entry.target.id];
          if (!link) return;
          if (entry.isIntersecting) {
            links.forEach(function (other) { other.removeAttribute("aria-current"); });
            link.setAttribute("aria-current", "true");
          }
        });
      },
      { rootMargin: "-10% 0px -75% 0px" }
    );
    Object.keys(byId).forEach(function (id) {
      var heading = document.getElementById(id);
      if (heading) observer.observe(heading);
    });
  }

  root.setAttribute("data-enhanced", "true");
})();
