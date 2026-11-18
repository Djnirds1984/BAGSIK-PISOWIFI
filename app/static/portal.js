/* Counter page helper: mirrors /coin/state so the coin slot shows live status.
   The captive portal ships its own inline script; this file only acts when a
   [data-kiosk] root is on the page. */
(function () {
  "use strict";

  var root = document.querySelector("[data-kiosk]");
  if (!root || !window.fetch) return;

  var els = {
    phase: root.querySelector("[data-coin-phase-label]"),
    msg: root.querySelector("[data-coin-msg]"),
    count: root.querySelector("[data-coin-count]"),
    arm: root.querySelector("[data-coin-arm]"),
    cancel: root.querySelector("[data-coin-cancel]"),
    balance: root.querySelector("[data-balance]")
  };

  var phase = root.getAttribute("data-phase") || "idle";
  var timer = 0;
  var polling = false;

  function money(centavos) {
    return "\u20b1" + (Math.max(0, centavos || 0) / 100).toFixed(2);
  }

  function plural(value, word) {
    value = Math.max(0, value | 0);
    return value + " " + word + (value === 1 ? "" : "s");
  }

  function text(seconds) {
    if (seconds <= 0) return "closed";
    return plural(seconds, "second") + " left";
  }

  function paint(data) {
    var txn = (data && data.txn) || {};
    var quote = (data && data.quote) || {};
    var granted = data && data.granted;
    var mine = phase === "listening" || phase === "counting" || phase === "processing";

    if (els.phase) els.phase.textContent = phase;
    if (els.balance) els.balance.textContent = money(data && data.balance_centavos);
    if (els.arm) els.arm.hidden = mine;
    if (els.cancel) els.cancel.hidden = !(phase === "listening" || phase === "counting");

    var message = "";
    if (phase === "listening") {
      message = "Listening for coins\u2026 drop them now (" + text(txn.seconds_left) + ").";
    } else if (phase === "counting") {
      message = "Counted " + plural(txn.pulses, "pulse") + " \u2248 " + plural(quote.minutes, "minute") +
        ". Keep dropping or wait for the slot to close.";
    } else if (phase === "processing") {
      message = "Processing\u2026 turning your coins into time.";
    } else if (phase === "busy") {
      message = (data && data.busy_message) || "Another customer is currently inserting coins. Please wait.";
    } else if (phase === "expired") {
      message = "No coins arrived before the slot closed. Tap Insert Coin to try again.";
    } else if (phase === "granted" && granted) {
      message = "Time added: " + plural(granted.minutes, "minute") + ".";
    } else {
      message = "Tap Insert Coin, then drop \u20b11, \u20b15, \u20b110 or \u20b120. There is no plan to choose - the posted coin rates decide your time.";
    }

    if (els.msg) els.msg.textContent = message;
    if (els.count) {
      els.count.textContent = phase === "busy" || phase === "listening"
        ? text(txn.seconds_left || timer)
        : "";
    }
  }

  function schedule() {
    var live = phase === "listening" || phase === "counting" ||
      phase === "processing" || phase === "busy";
    window.setTimeout(refresh, live ? 1000 : 4000);
  }

  function refresh() {
    if (polling) return;
    polling = true;
    fetch("/coin/state", { cache: "no-store", credentials: "same-origin" })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (!data) return;
        phase = data.phase || "idle";
        if (phase === "idle" && data.granted) phase = "granted";
        timer = (data.txn && data.txn.seconds_left) || 0;
        paint(data);
      })
      .catch(function () {})
      .then(function () {
        polling = false;
        schedule();
      });
  }

  root.addEventListener("submit", function (event) {
    var button = event.target.querySelector("button[type='submit'], button:not([type])");
    if (!button) return;
    var busy = event.target.getAttribute("data-busy") || "Please wait\u2026";
    window.setTimeout(function () {
      button.disabled = true;
      button.textContent = busy;
    }, 0);
  });

  refresh();
})();
