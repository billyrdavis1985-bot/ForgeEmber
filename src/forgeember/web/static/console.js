// The approval console shows a refused decision instead of silently ignoring it. The server
// answers 400 (no name, or approving a "check first" hold without saying how it was checked)
// or 409 (already decided) with the hold's row and the reason; htmx skips error responses
// by default, so let these two through. Nothing was recorded in either case.
document.addEventListener("htmx:beforeSwap", function (event) {
  var status = event.detail.xhr.status;
  if (status === 400 || status === 409) {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
});
