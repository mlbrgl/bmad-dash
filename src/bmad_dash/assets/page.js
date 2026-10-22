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
    var slugs = toggles.filter(isOpen).map(function (b) { return b.dataset.slug; });
    try { localStorage.setItem(key, JSON.stringify(slugs)); } catch (e) {}
  }

  var saved = null;
  try {
    var raw = localStorage.getItem(key);
    if (raw !== null) {
      var value = JSON.parse(raw);
      if (Array.isArray(value)) saved = value;
    }
  } catch (e) {}
  // Finished rows always start closed.
  if (saved !== null) {
    toggles.forEach(function (b) {
      var finished = b.closest(".is-finished") !== null;
      setOpen(b, !finished && saved.indexOf(b.dataset.slug) !== -1);
    });
  }

  toggles.forEach(function (button) {
    button.addEventListener("click", function () {
      setOpen(button, !isOpen(button));
      save();
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
