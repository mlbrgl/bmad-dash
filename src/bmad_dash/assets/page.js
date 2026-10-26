(function () {
  var key = document.querySelector("[data-storage-key]").getAttribute("data-storage-key");
  var toggles = Array.prototype.slice.call(document.querySelectorAll("button.toggle"));

  function isOpen(button) {
    return button.getAttribute("aria-expanded") === "true";
  }
  function setOpen(button, open) {
    button.setAttribute("aria-expanded", open ? "true" : "false");
    document.getElementById(button.getAttribute("aria-controls")).hidden = !open;
  }
  function save() {
    var keys = toggles.filter(isOpen).map(function (b) { return b.dataset.key; });
    try { localStorage.setItem(key, JSON.stringify(keys)); } catch (e) {}
  }

  var saved = null;
  try {
    var raw = localStorage.getItem(key);
    if (raw !== null) {
      var value = JSON.parse(raw);
      if (Array.isArray(value)) saved = value;
    }
  } catch (e) {}
  // Finished and close-only rows always start closed.
  if (saved !== null) {
    toggles.forEach(function (b) {
      var keepClosed = b.closest(".is-finished, [data-closed-start]") !== null;
      setOpen(b, !keepClosed && saved.indexOf(b.dataset.key) !== -1);
    });
  }

  toggles.forEach(function (button) {
    button.addEventListener("click", function () {
      setOpen(button, !isOpen(button));
      save();
    });
  });

  // A drag that selected text is not a click on the row.
  function selecting() {
    return String(window.getSelection()) !== "";
  }

  // A click on an empty part of an initiative row toggles its tree. Links,
  // buttons and forms act on their own (the toggle runs its own handler);
  // the open panel never toggles. A mouse shortcut: no extra tab stop.
  toggles.forEach(function (button) {
    var row = button.closest(".init");
    row.addEventListener("click", function (e) {
      if (e.target.closest(".panel")) return;
      if (e.target.closest("a, button, form, .place")) return;
      if (selecting()) return;
      button.click();
    });
  });

  // A click on an empty part of a tree row opens its file, like its name
  // link. Ctrl/Cmd, Shift and middle click open a new window or tab. Toggle rows and rows
  // with a plain-text name carry no `data-href`.
  function openRow(e) {
    if (e.button > 1) return; // right click: the context menu, not a click
    if (e.target.closest("a, button")) return;
    if (selecting()) return;
    var href = e.currentTarget.getAttribute("data-href");
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) {
      e.preventDefault();
      window.open(href);
    } else if (e.button === 0) {
      location.href = href;
    }
  }
  Array.prototype.forEach.call(document.querySelectorAll(".t-row[data-href]"), function (row) {
    row.addEventListener("click", openRow);
    row.addEventListener("auxclick", openRow);
  });

  // "Show n closed tickets" <-> "Hide closed tickets". Not remembered.
  Array.prototype.forEach.call(document.querySelectorAll("button.closed-toggle"), function (button) {
    button.addEventListener("click", function () {
      var open = !isOpen(button);
      setOpen(button, open);
      button.textContent = button.getAttribute(open ? "data-hide" : "data-show");
    });
  });

  // Show finished (n) <-> Hide finished. Not remembered.
  var more = document.getElementById("show-finished");
  if (more) {
    var label = more.textContent;
    more.addEventListener("click", function () {
      var open = !isOpen(more);
      setOpen(more, open);
      more.textContent = open ? "Hide finished" : label;
    });
  }

  // The VS Code failure shows once: drop `vscode_failed` from the URL so a
  // reload doesn't show it again. No reload, other params and the hash stay.
  var params = new URLSearchParams(location.search);
  if (params.has("vscode_failed")) {
    params.delete("vscode_failed");
    var search = params.toString();
    history.replaceState(
      null, "", location.pathname + (search ? "?" + search : "") + location.hash
    );
  }
})();
