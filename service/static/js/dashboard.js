/* Shared OmniBanner UI utilities (toasts, setup wizard, theme, logout, connection tests) */

function showToast(msg, type = "success") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = msg;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

/* Setup Wizard Stepper */
let currentStep = 1;
function showStep(step) {
    document.querySelectorAll(".setup-step-content").forEach(el => el.classList.remove("active"));
    const stepEl = document.getElementById(`step-${step}`);
    if (stepEl) stepEl.classList.add("active");

    document.querySelectorAll(".step-node").forEach(node => {
        const n = parseInt(node.getAttribute("data-step"), 10);
        node.classList.remove("active", "completed");
        if (n === step) node.classList.add("active");
        else if (n < step) node.classList.add("completed");
    });
    currentStep = step;
}

function nextStep() {
    if (currentStep < 4) showStep(currentStep + 1);
}

function prevStep() {
    if (currentStep > 1) showStep(currentStep - 1);
}

/* Tab Switcher */
function switchTab(tabId) {
    document.querySelectorAll(".tabs-nav .tab-btn").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-tab") === tabId);
    });
    document.querySelectorAll(".tab-pane").forEach(pane => {
        pane.classList.toggle("active", pane.id === tabId);
    });
    window.dispatchEvent(new CustomEvent("omnibanner:tab-changed", { detail: { tabId } }));
}

/* Theme & Logout */
async function toggleThemeMode() {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    document.body.removeAttribute("data-theme");
    try {
        await fetch("/api/me/theme", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ theme: next })
        });
    } catch (e) {}
}

async function performLogout() {
    let redirectTo = "/login";
    try {
        localStorage.removeItem("token");
        const res = await fetch("/api/logout", { method: "POST" });
        if (res.ok) {
            const data = await res.json();
            if (data && data.redirect_url) {
                redirectTo = data.redirect_url;
            }
        }
    } catch (e) {}
    window.location.href = redirectTo;
}

/* Shared Safe HTML Sanitizer (DOM Allowlist; supports optional Markdown block tags) */
function sanitizeHtmlToFragment(rawHtml, allowBlockTags = false) {
    const ALLOWED = new Set(["A", "STRONG", "B", "EM", "I", "U", "S", "CODE", "SPAN", "BR"]);
    if (allowBlockTags) {
        ["H1", "H2", "H3", "H4", "P", "UL", "OL", "LI", "PRE", "BLOCKQUOTE", "HR"].forEach(t => ALLOWED.add(t));
    }
    const frag = document.createDocumentFragment();
    if (!rawHtml) return frag;
    const doc = new DOMParser().parseFromString(String(rawHtml), "text/html");

    function walk(node, target) {
        for (const child of Array.from(node.childNodes)) {
            if (child.nodeType === Node.TEXT_NODE) {
                target.appendChild(document.createTextNode(child.textContent));
            } else if (child.nodeType === Node.ELEMENT_NODE) {
                const tag = child.tagName.toUpperCase();
                if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "SVG", "MATH"].includes(tag)) continue;
                if (!ALLOWED.has(tag)) {
                    walk(child, target);
                    continue;
                }
                const el = document.createElement(tag.toLowerCase());
                if (tag === "A") {
                    const href = (child.getAttribute("href") || "").trim();
                    const cleaned = href.replace(/[\x00-\x20\x7f]+/g, "");
                    if (/^(https?:\/\/|mailto:|\/(?!\/))/i.test(cleaned)) {
                        el.setAttribute("href", href);
                        if (/^https?:\/\//i.test(cleaned)) el.setAttribute("target", "_blank");
                        el.setAttribute("rel", "noopener noreferrer");
                    }
                    const title = child.getAttribute("title");
                    if (title) el.setAttribute("title", title);
                }
                walk(child, el);
                target.appendChild(el);
            }
        }
    }
    walk(doc.body, frag);
    return frag;
}

/* Setup Page Listeners */
document.addEventListener("DOMContentLoaded", () => {
    const authSelect = document.getElementById("setup-auth-mode");
    const oidcSection = document.getElementById("auth-oidc-section");
    if (authSelect && oidcSection) {
        const updateAuthMode = () => {
            oidcSection.style.display = authSelect.value === "oidc" ? "block" : "none";
        };
        authSelect.addEventListener("change", updateAuthMode);
        updateAuthMode();
    }
});

/* Shared SMTP & Kuma Test Handlers (used by both setup.html and dashboard.html) */
async function testSMTPConnection() {
    const host = (document.getElementById("smtp-host") || {}).value || "";
    const port = parseInt(((document.getElementById("smtp-port") || {}).value || "587"), 10);
    const tls_mode = (document.getElementById("smtp-tls-mode") || {}).value || "starttls";
    const username = (document.getElementById("smtp-username") || {}).value || "";
    const password = (document.getElementById("smtp-password") || {}).value || "";
    const from_email = (document.getElementById("smtp-from-email") || {}).value || "";
    const from_name = (document.getElementById("smtp-from-name") || {}).value || "OmniBanner Alerts";
    const to_email = ((document.getElementById("smtp-test-to") || {}).value || "").trim() || from_email;

    showToast("Testing SMTP connection...", "info");
    try {
        const response = await fetch("/api/test-smtp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ host, port, tls_mode, username, password, from_email, from_name, to_email })
        });
        const result = await response.json();
        if (response.ok && result.success) {
            showToast("SMTP connection verified! Test email sent.", "success");
        } else {
            showToast(`SMTP test failed: ${result.detail || "Check settings"}`, "error");
        }
    } catch (e) {
        showToast("Error sending SMTP test request", "error");
    }
}

async function testKumaConnection() {
    const url = (document.getElementById("kuma-url") || {}).value || "";
    const username = (document.getElementById("kuma-username") || {}).value || "";
    const password = (document.getElementById("kuma-password") || {}).value || "";
    const verifyEl = document.getElementById("kuma-verify-tls");
    const verify_tls = verifyEl ? verifyEl.checked : true;

    showToast("Testing Uptime Kuma connection...", "info");
    try {
        const response = await fetch("/api/test-kuma", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url, username, password, verify_tls })
        });
        const result = await response.json();
        if (response.ok && result.success) {
            showToast(result.detail || "Connected to Uptime Kuma!", "success");
        } else {
            showToast(`Kuma connection failed: ${result.detail || "Check credentials/URL"}`, "error");
        }
    } catch (e) {
        showToast("Error testing Uptime Kuma connection", "error");
    }
}
