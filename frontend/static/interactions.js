// Browser-only interactions; persistence, routing and decisions live in Python.
const toast = document.getElementById("toast");
let toastTimeout;
function notify(message) {
  document.getElementById("toast-message").textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => { toast.hidden = true; }, 4000);
}
document.querySelectorAll("[data-flash]").forEach(node => notify(node.dataset.flash));
document.addEventListener("click", event => {
  const button = event.target.closest("[data-href], [data-post], [data-message], [data-dismiss-toast], button[aria-label]");
  if (!button || button.disabled) return;
  if (button.dataset.href) window.location.assign(button.dataset.href);
  if (button.dataset.message) notify(button.dataset.message);
  if (button.hasAttribute("data-dismiss-toast")) toast.hidden = true;
  if (button.dataset.post) {
    const form = document.createElement("form");
    form.method = "post";
    form.action = button.dataset.post;
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = "csrf";
    input.value = document.querySelector('meta[name="csrf-token"]').content;
    form.append(input);
    document.body.append(form);
    form.submit();
  }
  if (button.getAttribute("aria-label") === "Open navigation") {
    const aside = document.querySelector("aside");
    aside.classList.toggle("hidden");
    aside.classList.toggle("flex");
    let backdrop = document.getElementById("navigation-backdrop");
    if (backdrop) backdrop.remove();
    else {
      backdrop = document.createElement("button");
      backdrop.id = "navigation-backdrop";
      backdrop.setAttribute("aria-label", "Close navigation");
      backdrop.className = "fixed inset-0 z-30 bg-black/30 lg:hidden";
      backdrop.onclick = () => { aside.classList.add("hidden"); aside.classList.remove("flex"); backdrop.remove(); };
      document.body.append(backdrop);
    }
  }
  if (button.getAttribute("aria-label") === "Notifications") {
    const old = document.getElementById("notifications");
    if (old) return old.remove();
    const panel = document.createElement("div");
    panel.id = "notifications";
    panel.className = "rounded-xl border border-[#e7ebe8] bg-white shadow-[0_2px_4px_#183b2303] absolute right-0 top-9 z-20 w-72 p-4 text-xs";
    
    const heading = document.createElement("h3");
    heading.className = "mb-3 font-semibold";
    heading.textContent = "Notifications";
    panel.appendChild(heading);

    let raw = button.getAttribute("data-notifications");
    let items = [];
    try { if (raw) items = JSON.parse(raw); } catch (e) {}

    if (items && items.length > 0) {
      items.slice(0, 4).forEach((item, idx) => {
        const p = document.createElement("p");
        p.className = idx > 0 ? "mt-3 leading-5 text-[#78857b]" : "leading-5";

        const titleNode = document.createTextNode(item.title || "");
        p.appendChild(titleNode);
        p.appendChild(document.createElement("br"));

        const span = document.createElement("span");
        span.className = "text-[10px] text-[#8c9690]";
        span.textContent = item.detail || "";
        p.appendChild(span);

        panel.appendChild(p);
      });
    } else {
      const p = document.createElement("p");
      p.className = "leading-5 text-[#78857b]";
      p.textContent = "No new notifications.";
      panel.appendChild(p);
    }
    button.parentElement.append(panel);
  }
});
document.querySelector("[data-year]")?.addEventListener("input", event => {
  document.querySelector("[data-age]").value = `${Math.max(0, new Date().getFullYear() - Number(event.target.value))} years`;
});
const MAX_FILE_SIZE = 5 * 1024 * 1024;
const ALLOWED_IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp", ".gif"];
const ALLOWED_DOC_EXTS = [".pdf", ".jpg", ".jpeg", ".png"];

document.querySelectorAll('input[type="file"]').forEach(input => {
  input.addEventListener("change", () => {
    const file = input.files[0];
    const defaultText = input.name === "image" ? "PNG, JPG, WEBP, GIF · up to 5 MB" : "PDF, JPG, PNG · up to 5 MB";
    const textSpan = input.parentElement.querySelectorAll("span")[1];
    if (!file) {
      if (textSpan) textSpan.textContent = defaultText;
      return;
    }
    if (file.size === 0) {
      input.value = "";
      if (textSpan) textSpan.textContent = defaultText;
      notify("Uploaded file is empty. Please select a valid file.");
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      input.value = "";
      if (textSpan) textSpan.textContent = defaultText;
      notify("File must be 5 MB or smaller.");
      return;
    }
    const nameLower = file.name.toLowerCase();
    const allowed = input.name === "image" ? ALLOWED_IMAGE_EXTS : ALLOWED_DOC_EXTS;
    const hasValidExt = allowed.some(ext => nameLower.endsWith(ext));
    if (!hasValidExt) {
      input.value = "";
      if (textSpan) textSpan.textContent = defaultText;
      notify("Unsupported file format. Please select a supported file.");
      return;
    }
    if (textSpan) {
      textSpan.textContent = input.name === "image" ? "Image attached ✓" : file.name;
    }
  });
});

document.querySelector("form[enctype='multipart/form-data']")?.addEventListener("submit", event => {
  const fileInputs = event.target.querySelectorAll('input[type="file"]');
  for (const input of fileInputs) {
    const file = input.files[0];
    if (file) {
      if (file.size > MAX_FILE_SIZE) {
        event.preventDefault();
        notify("File must be 5 MB or smaller.");
        return;
      }
      if (file.size === 0) {
        event.preventDefault();
        notify("Uploaded file is empty. Please select a valid file.");
        return;
      }
    }
  }
});
document.querySelector('select[aria-label="Date range"]')?.addEventListener("change", event => {
  window.location.assign("/?period=" + encodeURIComponent(event.target.value));
});
let searchTimeout;
document.querySelector("[data-device-search]")?.addEventListener("input", event => {
  clearTimeout(searchTimeout);
  const query = event.target.value;
  searchTimeout = setTimeout(async () => {
    const response = await fetch("/devices?q=" + encodeURIComponent(query));
    if (!response.ok) return;
    const next = new DOMParser().parseFromString(await response.text(), "text/html");
    document.querySelector("main table").parentElement.replaceWith(next.querySelector("main table").parentElement);
    history.replaceState(null, "", "/devices?q=" + encodeURIComponent(query));
  }, 200);
});
