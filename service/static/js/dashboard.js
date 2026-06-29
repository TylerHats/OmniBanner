// Toast notifications
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
    }, 3000);
}

// Setup Wizard Flow
let currentStep = 1;
function showStep(step) {
    document.querySelectorAll(".setup-step-content").forEach(el => {
        el.classList.remove("active");
    });
    const stepEl = document.getElementById(`step-${step}`);
    if (stepEl) stepEl.classList.add("active");
    
    // Update step nodes
    document.querySelectorAll(".step-node").forEach(node => {
        const n = parseInt(node.getAttribute("data-step"));
        node.classList.remove("active", "completed");
        if (n === step) {
            node.classList.add("active");
        } else if (n < step) {
            node.classList.add("completed");
        }
    });
    
    currentStep = step;
}

function nextStep() {
    if (currentStep < 4) {
        showStep(currentStep + 1);
    }
}

function prevStep() {
    if (currentStep > 1) {
        showStep(currentStep - 1);
    }
}

// Live Banner Preview Sync
function syncBannerPreview() {
    const preview = document.getElementById("simulated-banner-el");
    if (!preview) return;
    
    const textInput = document.getElementById("notice-text");
    const bgInput = document.getElementById("notice-bg-color");
    const textColInput = document.getElementById("notice-text-color");
    const dismissInput = document.getElementById("notice-dismissible");
    
    if (textInput) {
        // Handle fallback text if empty
        preview.textContent = textInput.value || "⚡ Notice Banner Live Preview";
    }
    if (bgInput) {
        preview.style.backgroundColor = bgInput.value;
    }
    if (textColInput) {
        preview.style.color = textColInput.value;
    }
    
    // Toggle close button representation
    const existingClose = preview.querySelector(".dismiss-btn");
    if (existingClose) existingClose.remove();
    
    if (dismissInput && dismissInput.checked) {
        const closeBtn = document.createElement("button");
        closeBtn.className = "dismiss-btn";
        closeBtn.innerHTML = "&times;";
        closeBtn.style.color = textColInput ? textColInput.value : "#ffffff";
        preview.appendChild(closeBtn);
    }
}

// Attach event listeners for real-time live preview update
document.addEventListener("DOMContentLoaded", () => {
    // Only run preview sync on elements that exist (i.e. on the dashboard page)
    const inputs = ["notice-text", "notice-bg-color", "notice-text-color", "notice-dismissible"];
    inputs.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener("input", syncBannerPreview);
            el.addEventListener("change", syncBannerPreview);
        }
    });
    
    // Toggle TOTP section visibility on setup page
    const mfaCheckbox = document.getElementById("enable-totp-setup");
    const totpCredentialsSection = document.getElementById("totp-credentials-section");
    if (mfaCheckbox && totpCredentialsSection) {
        mfaCheckbox.addEventListener("change", (e) => {
            totpCredentialsSection.style.display = e.target.checked ? "block" : "none";
        });
    }

    // Toggle Admin Mode on setup page
    const authSelect = document.getElementById("setup-auth-mode");
    const localSection = document.getElementById("auth-local-section");
    const oidcSection = document.getElementById("auth-oidc-section");
    if (authSelect && localSection && oidcSection) {
        authSelect.addEventListener("change", (e) => {
            if (e.target.value === "oidc") {
                localSection.style.display = "none";
                oidcSection.style.display = "block";
            } else {
                localSection.style.display = "block";
                oidcSection.style.display = "none";
            }
        });
    }

    // Initial preview render if preview exists
    syncBannerPreview();
});

// Tab navigation handler
function switchTab(tabId) {
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.classList.remove("active");
    });
    document.querySelectorAll(".tab-pane").forEach(pane => {
        pane.classList.remove("active");
    });
    
    const activeBtn = document.querySelector(`[onclick="switchTab('${tabId}')"]`);
    const activePane = document.getElementById(tabId);
    
    if (activeBtn) activeBtn.classList.add("active");
    if (activePane) activePane.classList.add("active");
}

// SMTP Test Function
async function testSMTPConnection() {
    const host = document.getElementById("smtp-host").value;
    const port = document.getElementById("smtp-port").value;
    const username = document.getElementById("smtp-username").value;
    const password = document.getElementById("smtp-password").value;
    const from_email = document.getElementById("smtp-from-email").value;
    const from_name = document.getElementById("smtp-from-name").value;
    
    showToast("Testing SMTP connection...", "info");
    
    try {
        const response = await fetch("/api/test-smtp", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${localStorage.getItem("token") || ""}`
            },
            body: JSON.stringify({ host, port: parseInt(port), username, password, from_email, from_name })
        });
        
        const result = await response.json();
        if (response.ok && result.success) {
            showToast("SMTP Connection Successful! Test email sent.", "success");
        } else {
            showToast(`SMTP Connection Failed: ${result.detail || result.message || "Unknown error"}`, "error");
        }
    } catch (e) {
        showToast("Error sending SMTP test request", "error");
    }
}

// Uptime Kuma Test Function
async function testKumaConnection() {
    const url = document.getElementById("kuma-url").value;
    const username = document.getElementById("kuma-username").value;
    const password = document.getElementById("kuma-password").value;
    
    showToast("Testing Uptime Kuma connection...", "info");
    
    try {
        const response = await fetch("/api/test-kuma", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${localStorage.getItem("token") || ""}`
            },
            body: JSON.stringify({ url, username, password })
        });
        
        const result = await response.json();
        if (response.ok && result.success) {
            showToast("Connected to Uptime Kuma successfully!", "success");
        } else {
            showToast(`Uptime Kuma connection failed: ${result.detail || "Credentials error"}`, "error");
        }
    } catch (e) {
        showToast("Error sending Uptime Kuma test request", "error");
    }
}
