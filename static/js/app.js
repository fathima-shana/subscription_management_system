// Global UI helpers. Kept tiny on purpose: HTMX + Alpine do the heavy lifting.
(function () {
  const root = () => document.getElementById("toast-root");
  const palette = {
    success: "border-emerald-200 bg-emerald-50 text-emerald-800",
    error: "border-rose-200 bg-rose-50 text-rose-800",
    warning: "border-amber-200 bg-amber-50 text-amber-800",
    info: "border-slate-200 bg-white text-slate-800",
  };

  function dismiss(el) {
    el.style.transition = "opacity 200ms";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 200);
  }

  window.toast = function (message, level = "info") {
    const el = document.createElement("div");
    el.className = `toast pointer-events-auto rounded-lg border px-4 py-3 text-sm shadow-lg ${palette[level] || palette.info}`;
    el.textContent = message; // textContent, never innerHTML (XSS-safe)
    root().appendChild(el);
    setTimeout(() => dismiss(el), 4000);
  };

  // Auto-dismiss server-rendered toasts.
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("#toast-root .toast").forEach((el) => setTimeout(() => dismiss(el), 4000));
  });

  // Views can trigger toasts from HTMX responses with: HX-Trigger: {"toast": {"message": "...", "level": "success"}}
  document.body.addEventListener("toast", (e) => window.toast(e.detail.message, e.detail.level));

  // Surface failed HTMX requests instead of failing silently.
  document.body.addEventListener("htmx:responseError", (e) => {
    window.toast(`Request failed (${e.detail.xhr.status}). Please try again.`, "error");
  });
})();
