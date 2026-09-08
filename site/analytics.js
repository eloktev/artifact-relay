// Privacy-first Google Analytics for the public Artifact Relay landing pages.
(() => {
  "use strict";

  const MEASUREMENT_ID = "G-LNSQNESC6L";
  const CONSENT_KEY = "artifact-relay.analytics-consent.v1";
  const GRANTED = "granted";
  const DENIED = "denied";
  let analyticsLoaded = false;

  if (window.location.hostname !== "artifact-relay.lok-labs.com") return;

  const readConsent = () => {
    try {
      return window.localStorage.getItem(CONSENT_KEY);
    } catch (_) {
      return null;
    }
  };

  const saveConsent = (value) => {
    try {
      window.localStorage.setItem(CONSENT_KEY, value);
    } catch (_) {
      // A blocked storage API keeps consent session-only.
    }
  };

  const gtag = function () {
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push(arguments);
  };

  const loadAnalytics = () => {
    if (analyticsLoaded) return;
    analyticsLoaded = true;
    window.dataLayer = window.dataLayer || [];
    window.gtag = gtag;
    gtag("consent", "default", {
      analytics_storage: "granted",
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
    });
    gtag("js", new Date());
    const safePageLocation = `${window.location.origin}${window.location.pathname}`;
    gtag("config", MEASUREMENT_ID, {
      send_page_view: false,
      page_location: safePageLocation,
      page_referrer: "",
      ignore_referrer: true,
      allow_google_signals: false,
      allow_ad_personalization_signals: false,
    });
    gtag("event", "page_view", {
      page_location: safePageLocation,
      page_path: window.location.pathname,
      page_referrer: "",
      page_title: document.title,
    });

    const tag = document.createElement("script");
    tag.id = "artifact-relay-google-tag";
    tag.async = true;
    tag.src = `https://www.googletagmanager.com/gtag/js?id=${MEASUREMENT_ID}`;
    document.head.appendChild(tag);
  };

  const sendEvent = (eventName) => {
    if (!analyticsLoaded || readConsent() !== GRANTED) return;
    gtag("event", eventName, { transport_type: "beacon" });
  };

  const eventForLink = (anchor) => {
    const rawHref = anchor.getAttribute("href");
    if (!rawHref) return null;
    if (rawHref.startsWith("hermes://plugin/install")) return "cta_plugin_install";

    let target;
    try {
      target = new URL(rawHref, window.location.origin);
    } catch (_) {
      return null;
    }

    if (target.hostname === "relay.lok-labs.com") return "cta_managed_connect";
    if (target.hostname === "artifact-relay.lok-labs.com") {
      if (target.pathname === "/self-host/") return "cta_self_host";
      if (target.pathname === "/guides/secure-agent-publishing/") return "cta_secure_guide";
      return null;
    }
    if (target.hostname !== "github.com") return null;
    if (target.pathname === "/eloktev/hermes-artifact-relay") return "cta_plugin_repository";
    if (target.pathname.startsWith("/eloktev/artifact-relay/releases/")) {
      return "cta_release_notes";
    }
    if (target.pathname === "/eloktev/artifact-relay/blob/main/docs/VPS.md") {
      return "cta_vps_guide";
    }
    if (target.pathname === "/eloktev/artifact-relay") return "cta_github_repository";
    if (target.pathname.startsWith("/eloktev/artifact-relay#")) {
      return "cta_github_repository";
    }
    return null;
  };

  const copyEvents = new Map([
    ["local-code", "copy_install_commands"],
    ["clone-code", "copy_install_commands"],
    ["build-code", "copy_install_commands"],
    ["bootstrap-code", "copy_install_commands"],
    ["start-code", "copy_install_commands"],
    ["publish-code", "copy_publish_commands"],
    ["plugin-code", "copy_plugin_commands"],
  ]);

  const buildConsentUi = () => {
    const panel = document.createElement("aside");
    panel.className = "analytics-consent";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-labelledby", "analytics-consent-title");
    panel.setAttribute("aria-describedby", "analytics-consent-description");

    const title = document.createElement("h2");
    title.id = "analytics-consent-title";
    title.textContent = "Optional analytics";

    const description = document.createElement("p");
    description.id = "analytics-consent-description";
    description.textContent =
      "Allow Google Analytics to measure page visits and named CTA actions. Artifact content, credentials, form values, and tenant URLs are never sent.";

    const actions = document.createElement("div");
    actions.className = "analytics-consent-actions";

    const allow = document.createElement("button");
    allow.type = "button";
    allow.className = "button button-primary";
    allow.textContent = "Allow analytics";

    const decline = document.createElement("button");
    decline.type = "button";
    decline.className = "button button-secondary";
    decline.textContent = "Decline";

    const settings = document.createElement("button");
    settings.type = "button";
    settings.className = "analytics-settings";
    settings.textContent = "Analytics choices";
    settings.setAttribute("aria-label", "Change analytics consent");

    actions.append(allow, decline);
    panel.append(title, description, actions);
    document.body.append(panel, settings);

    const close = () => {
      panel.hidden = true;
      settings.hidden = false;
    };
    const open = () => {
      panel.hidden = false;
      settings.hidden = true;
      (readConsent() === GRANTED ? decline : allow).focus();
    };

    allow.addEventListener("click", () => {
      saveConsent(GRANTED);
      loadAnalytics();
      sendEvent("analytics_consent_granted");
      close();
    });
    decline.addEventListener("click", () => {
      saveConsent(DENIED);
      if (analyticsLoaded) {
        gtag("consent", "update", {
          analytics_storage: "denied",
          ad_storage: "denied",
          ad_user_data: "denied",
          ad_personalization: "denied",
        });
        window.location.reload();
        return;
      }
      close();
    });
    settings.addEventListener("click", open);

    if (readConsent() === null) open();
    return panel;
  };

  const start = () => {
    if (readConsent() === GRANTED) loadAnalytics();
    buildConsentUi();

    document.addEventListener("click", (event) => {
      const anchor = event.target.closest("a");
      if (anchor) {
        const eventName = eventForLink(anchor);
        if (eventName) sendEvent(eventName);
      }
    });
    document.addEventListener("artifact-relay:copy-success", (event) => {
      const eventName = copyEvents.get(event.detail?.copyTarget);
      if (eventName) sendEvent(eventName);
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
