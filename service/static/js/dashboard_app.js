/* OmniBanner Dashboard Application Controller */
(function () {
    "use strict";

    let noticesCache = [];
    let sitesCache = [];
    let templatesCache = [];
    let oidcCache = [];
    let currentUser = null;
    let brandCache = {};
    let previewDarkSite = false;

    async function apiJson(url, opts = {}) {
        const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
        const response = await fetch(url, Object.assign({}, opts, { headers }));
        let data = null;
        try {
            data = await response.json();
        } catch (e) {}
        if (!response.ok) {
            throw new Error((data && data.detail) || `HTTP ${response.status}`);
        }
        return data;
    }

    window.copyTextById = function (elId, okMsg) {
        const el = document.getElementById(elId);
        if (!el) return;
        navigator.clipboard.writeText(el.textContent).then(() => showToast(okMsg || "Copied!", "success"));
    };

    /* --- INITIALIZATION --- */
    document.addEventListener("DOMContentLoaded", async () => {
        const origin = window.location.origin;
        const helper = document.getElementById("api-endpoint-helper");
        if (helper) helper.textContent = origin;

        const embedBox = document.getElementById("universal-embed-snippet");
        if (embedBox) {
            embedBox.textContent = `<script src="${origin}/embed.js" data-api="${origin}" async><\/script>`;
        }

        attachLivePreviewListeners();
        attachFormToggles();

        try {
            currentUser = await apiJson("/api/me");
        } catch (e) {}

        applyRolePermissions();

        await Promise.all([
            loadBranding(),
            loadTemplates(),
            loadNotices(),
            loadSites(),
            loadSMTP(),
            loadSubscribers(),
            loadKumaSettings(),
            loadSecurity(),
        ]);

        if (currentUser && currentUser.role === "admin") {
            await Promise.all([
                loadOIDCProviders(),
                loadUsers(),
                loadWebhooks(),
                loadInboundRules(),
                loadAuditLogs(),
            ]);
        }

        window.addEventListener("omnibanner:tab-changed", (ev) => {
            const tabId = ev.detail && ev.detail.tabId;
            if (tabId === "tab-banners") loadNotices();
            else if (tabId === "tab-sites") loadSites();
            else if (tabId === "tab-smtp") loadSubscribers();
            else if (tabId === "tab-integrations" && currentUser && currentUser.role === "admin") {
                loadWebhooks();
                loadInboundRules();
            } else if (tabId === "tab-auth" && currentUser && currentUser.role === "admin") {
                loadOIDCProviders();
                loadUsers();
                loadAuditLogs();
            }
        });
    });

    function applyRolePermissions() {
        if (!currentUser) return;
        const isAdmin = currentUser.role === "admin";
        const isEditor = currentUser.role === "admin" || currentUser.role === "editor";

        if (!isAdmin) {
            ["tab-integrations", "tab-auth", "tab-branding"].forEach(tab => {
                const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
                if (btn) btn.style.display = "none";
            });
            const rotBtn = document.getElementById("site-rotate-key-btn");
            if (rotBtn) rotBtn.style.display = "none";
        }
        if (!isEditor) {
            ["notice-form", "smtp-form"].forEach(fid => {
                const form = document.getElementById(fid);
                if (form) {
                    form.querySelectorAll("button[type='submit']").forEach(b => { b.style.display = "none"; });
                }
            });
        }
    }

    window.closeModal = function (modalId) {
        const el = document.getElementById(modalId);
        if (el) el.classList.remove("open");
    };

    function attachFormToggles() {
        const targetTypeEl = document.getElementById("notice-target-type");
        if (targetTypeEl) {
            targetTypeEl.addEventListener("change", () => toggleTargetInputs(targetTypeEl.value));
        }
        const sendSmtpEl = document.getElementById("notice-send-smtp");
        if (sendSmtpEl) {
            sendSmtpEl.addEventListener("change", () => {
                document.getElementById("notice-smtp-settings").style.display = sendSmtpEl.checked ? "block" : "none";
            });
        }
        const kumaEl = document.getElementById("notice-uptime-kuma-integration");
        if (kumaEl) {
            kumaEl.addEventListener("change", () => {
                document.getElementById("notice-kuma-settings").style.display = kumaEl.checked ? "block" : "none";
            });
        }
        const searchEl = document.getElementById("notices-search");
        if (searchEl) {
            searchEl.addEventListener("input", renderNoticesTable);
        }
        const oidcNameEl = document.getElementById("oidc-name");
        const oidcSlugEl = document.getElementById("oidc-slug");
        if (oidcNameEl && oidcSlugEl) {
            const updateCb = () => {
                const rawSlug = oidcSlugEl.value || oidcNameEl.value || "authentik";
                const slug = rawSlug.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-|-$/g, "") || "sso";
                const base = (brandCache.public_base_url || window.location.origin).replace(/\/$/, "");
                document.getElementById("oidc-callback-preview").textContent = `${base}/auth/oidc/${slug}/callback`;
            };
            oidcNameEl.addEventListener("input", updateCb);
            oidcSlugEl.addEventListener("input", updateCb);
            updateCb();
        }
    }

    function toggleTargetInputs(val) {
        const sitesWrap = document.getElementById("target-sites-wrapper");
        const groupsWrap = document.getElementById("target-groups-wrapper");
        if (sitesWrap) sitesWrap.style.display = val === "domains" || val === "sites" ? "block" : "none";
        if (groupsWrap) groupsWrap.style.display = val === "groups" ? "block" : "none";
    }

    /* --- LIVE PREVIEW --- */
    function attachLivePreviewListeners() {
        const ids = [
            "notice-text", "notice-bg-color", "notice-text-color", "notice-dismissible",
            "notice-icon", "notice-cta-text", "notice-cta-url", "notice-cta-bg",
            "notice-cta-text-color", "notice-show-countdown", "notice-email-subject",
            "notice-email-text", "notice-kuma-text", "notice-title"
        ];
        ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener("input", syncBannerPreview);
                el.addEventListener("change", syncBannerPreview);
            }
        });
        syncBannerPreview();
    }

    window.switchPreviewPane = function (paneId, btn) {
        document.querySelectorAll(".preview-tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".preview-viewport-pane").forEach(p => p.classList.remove("active"));
        if (btn) btn.classList.add("active");
        const pane = document.getElementById(paneId);
        if (pane) pane.classList.add("active");
    };

    window.togglePreviewDevice = function () {
        const frame = document.getElementById("preview-browser-frame");
        const btn = document.getElementById("preview-device-btn");
        const isMob = frame.classList.toggle("mobile-mode");
        btn.textContent = isMob ? "🖥️ Desktop" : "📱 Mobile";
    };

    window.togglePreviewSiteBg = function () {
        previewDarkSite = !previewDarkSite;
        const vp = document.getElementById("preview-browser-viewport");
        const btn = document.getElementById("preview-bg-btn");
        vp.classList.toggle("dark-site", previewDarkSite);
        btn.textContent = previewDarkSite ? "☀️ Light Site" : "🌙 Dark Site";
    };

    function syncBannerPreview() {
        const preview = document.getElementById("simulated-banner-el");
        if (!preview) return;

        const text = (document.getElementById("notice-text") || {}).value || "";
        const icon = (document.getElementById("notice-icon") || {}).value || "";
        const bg = (document.getElementById("notice-bg-color") || {}).value || "#ef4444";
        const fg = (document.getElementById("notice-text-color") || {}).value || "#ffffff";
        const ctaText = (document.getElementById("notice-cta-text") || {}).value || "";
        const ctaBg = (document.getElementById("notice-cta-bg") || {}).value || "#ffffff";
        const ctaFg = (document.getElementById("notice-cta-text-color") || {}).value || "#0f172a";
        const dismissible = (document.getElementById("notice-dismissible") || {}).checked;
        const showCountdown = (document.getElementById("notice-show-countdown") || {}).checked;

        preview.style.backgroundColor = bg;
        preview.style.color = fg;
        preview.innerHTML = "";

        if (icon) {
            const ic = document.createElement("span");
            ic.textContent = icon;
            preview.appendChild(ic);
        }

        const msgSpan = document.createElement("span");
        msgSpan.appendChild(sanitizeHtmlToFragment(text || "⚡ Notice Banner Live Preview"));
        preview.appendChild(msgSpan);

        if (showCountdown) {
            const cd = document.createElement("span");
            cd.className = "simulated-countdown";
            cd.textContent = "01:45:00";
            preview.appendChild(cd);
        }

        if (ctaText) {
            const cta = document.createElement("span");
            cta.className = "simulated-cta";
            cta.style.backgroundColor = ctaBg;
            cta.style.color = ctaFg;
            cta.textContent = ctaText;
            preview.appendChild(cta);
        }

        if (dismissible) {
            const closeBtn = document.createElement("button");
            closeBtn.type = "button";
            closeBtn.className = "dismiss-btn";
            closeBtn.innerHTML = "&times;";
            closeBtn.style.color = fg;
            preview.appendChild(closeBtn);
        }

        // Email preview
        const subj = (document.getElementById("notice-email-subject") || {}).value ||
            `[${brandCache.app_name || "OmniBanner"}] ${(document.getElementById("notice-title") || {}).value || text.slice(0, 40) || "Notice"}`;
        const emailBodyRaw = (document.getElementById("notice-email-text") || {}).value || text || "Notice message preview";
        const subjEl = document.getElementById("preview-email-subject");
        const bodyEl = document.getElementById("preview-email-body");
        if (subjEl) subjEl.textContent = `Subject: ${subj}`;
        if (bodyEl) {
            bodyEl.innerHTML = "";
            bodyEl.appendChild(sanitizeHtmlToFragment(emailBodyRaw.replace(/\n/g, "<br>")));
        }

        // Kuma Markdown preview (preserve block tags safely)
        const kumaRaw = (document.getElementById("notice-kuma-text") || {}).value || text || "No maintenance description yet.";
        const kumaEl = document.getElementById("preview-kuma-body");
        if (kumaEl) {
            if (typeof marked !== "undefined" && marked.parse) {
                const rawRendered = marked.parse(kumaRaw);
                kumaEl.innerHTML = "";
                kumaEl.appendChild(sanitizeHtmlToFragment(rawRendered, true));
            } else {
                kumaEl.textContent = kumaRaw;
            }
        }
    }

    /* --- TEMPLATES --- */
    async function loadTemplates() {
        try {
            templatesCache = await apiJson("/api/templates");
            const container = document.getElementById("template-pills-container");
            if (!container) return;
            container.innerHTML = "";
            templatesCache.forEach(tpl => {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "template-pill";
                btn.textContent = `${tpl.icon ? tpl.icon + " " : ""}${tpl.name}`;
                btn.addEventListener("click", () => applyTemplate(tpl));
                container.appendChild(btn);
            });
        } catch (e) {}
    }

    function applyTemplate(tpl) {
        document.getElementById("notice-text").value = tpl.banner_text || "";
        document.getElementById("notice-bg-color").value = tpl.bg_color || "#3b82f6";
        document.getElementById("notice-text-color").value = tpl.text_color || "#ffffff";
        document.getElementById("notice-position").value = tpl.position || "top";
        document.getElementById("notice-icon").value = tpl.icon || "";
        document.getElementById("notice-cta-text").value = tpl.cta_text || "";
        document.getElementById("notice-cta-url").value = tpl.cta_url || "";
        document.getElementById("notice-cta-bg").value = tpl.cta_bg_color || "#ffffff";
        document.getElementById("notice-cta-text-color").value = tpl.cta_text_color || "#0f172a";
        document.getElementById("notice-dismissible").checked = Boolean(tpl.dismissible);
        document.getElementById("notice-dismiss-cooldown").value = tpl.dismiss_cooldown || 24;
        document.getElementById("notice-display-duration").value = tpl.display_duration || 0;
        document.getElementById("notice-show-countdown").checked = Boolean(tpl.show_countdown);
        syncBannerPreview();
        showToast(`Applied "${tpl.name}" preset`, "info");
    }

    window.saveAsTemplate = function () {
        document.getElementById("preset-name-input").value = "";
        document.getElementById("save-preset-modal").classList.add("open");
    };

    window.submitSavePreset = async function (event) {
        event.preventDefault();
        const name = document.getElementById("preset-name-input").value.trim();
        if (!name) return;
        try {
            await apiJson("/api/templates", {
                method: "POST",
                body: JSON.stringify({
                    name,
                    banner_text: document.getElementById("notice-text").value,
                    bg_color: document.getElementById("notice-bg-color").value,
                    text_color: document.getElementById("notice-text-color").value,
                    position: document.getElementById("notice-position").value,
                    icon: document.getElementById("notice-icon").value,
                    cta_text: document.getElementById("notice-cta-text").value,
                    cta_url: document.getElementById("notice-cta-url").value,
                    cta_bg_color: document.getElementById("notice-cta-bg").value,
                    cta_text_color: document.getElementById("notice-cta-text-color").value,
                    dismissible: document.getElementById("notice-dismissible").checked,
                    dismiss_cooldown: parseInt(document.getElementById("notice-dismiss-cooldown").value || "24", 10),
                    display_duration: parseInt(document.getElementById("notice-display-duration").value || "0", 10),
                    show_countdown: document.getElementById("notice-show-countdown").checked
                })
            });
            closeModal("save-preset-modal");
            showToast("Preset saved!", "success");
            loadTemplates();
        } catch (err) {
            showToast(err.message, "error");
        }
    };

    /* --- NOTICES CRUD --- */
    function toLocalInputValue(isoUtc) {
        if (!isoUtc) return "";
        const d = new Date(isoUtc);
        const shifted = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
        return shifted.toISOString().slice(0, 16);
    }

    function fromLocalInputToUtcIso(val) {
        if (!val) return "";
        return new Date(val).toISOString();
    }

    window.resetNoticeForm = function () {
        document.getElementById("editor-action-title").textContent = "Create Scheduled Notice";
        document.getElementById("notice-form").reset();
        document.getElementById("notice-id").value = "";
        document.getElementById("notice-bg-color").value = "#ef4444";
        document.getElementById("notice-text-color").value = "#ffffff";
        document.getElementById("notice-cta-bg").value = "#ffffff";
        document.getElementById("notice-cta-text-color").value = "#0f172a";
        document.getElementById("notice-priority").value = "10";
        document.getElementById("notice-recurrence").value = "none";
        document.getElementById("notice-email-lead").value = "0";
        document.getElementById("notice-enabled").checked = true;
        document.getElementById("notice-dismissible").checked = true;
        document.getElementById("notice-smtp-settings").style.display = "none";
        document.getElementById("notice-kuma-settings").style.display = "none";
        toggleTargetInputs("all");
        syncBannerPreview();
    };

    window.setNoticesView = function (mode) {
        const tableWrap = document.getElementById("notices-table-wrap");
        const tlWrap = document.getElementById("notices-timeline-wrap");
        const tableBtn = document.getElementById("view-mode-table");
        const tlBtn = document.getElementById("view-mode-timeline");
        if (mode === "timeline") {
            tableWrap.style.display = "none";
            tlWrap.style.display = "flex";
            tableBtn.className = "btn btn-secondary btn-sm";
            tlBtn.className = "btn btn-primary btn-sm";
            renderNoticesTimeline();
        } else {
            tableWrap.style.display = "block";
            tlWrap.style.display = "none";
            tableBtn.className = "btn btn-primary btn-sm";
            tlBtn.className = "btn btn-secondary btn-sm";
        }
    };

    function renderNoticesTimeline() {
        const container = document.getElementById("notices-timeline-wrap");
        if (!container) return;
        const query = ((document.getElementById("notices-search") || {}).value || "").trim().toLowerCase();
        const items = noticesCache
            .filter(n => !query || `${n.title} ${n.banner_text} ${n.target_sites} ${n.target_groups}`.toLowerCase().includes(query))
            .slice()
            .sort((a, b) => new Date(a.scheduled_start) - new Date(b.scheduled_start));

        container.innerHTML = "";
        if (!items.length) {
            const p = document.createElement("p");
            p.className = "text-muted";
            p.style.textAlign = "center";
            p.textContent = "No notices to show on timeline.";
            container.appendChild(p);
            return;
        }
        items.forEach(n => {
            const card = document.createElement("div");
            card.style.borderLeft = `4px solid ${n.bg_color || "#8b5cf6"}`;
            card.style.padding = "0.85rem 1rem";
            card.style.background = "var(--bg-input)";
            card.style.borderRadius = "8px";
            card.style.display = "flex";
            card.style.justifyContent = "space-between";
            card.style.alignItems = "center";
            card.style.gap = "1rem";
            card.style.flexWrap = "wrap";

            const left = document.createElement("div");
            const title = document.createElement("strong");
            title.textContent = `${n.icon ? n.icon + " " : ""}${n.title || n.banner_text.slice(0, 60)}`;
            const meta = document.createElement("div");
            meta.className = "text-muted";
            meta.style.fontSize = "0.78rem";
            meta.textContent = `${new Date(n.scheduled_start).toLocaleString()} → ${new Date(n.scheduled_end).toLocaleString()} (${n.recurrence && n.recurrence !== "none" ? "repeats " + n.recurrence : "one-time"})`;
            left.append(title, meta);

            const right = document.createElement("div");
            right.className = "d-flex align-center";
            const badge = document.createElement("span");
            badge.className = `badge ${{ active: "badge-success", scheduled: "badge-warning", paused: "badge-info", expired: "badge-muted" }[n.status] || "badge-muted"}`;
            badge.textContent = n.status;
            const editBtn = document.createElement("button");
            editBtn.type = "button";
            editBtn.className = "btn btn-secondary btn-sm";
            editBtn.textContent = "Edit";
            editBtn.addEventListener("click", () => editNotice(n));
            right.append(badge, editBtn);

            card.append(left, right);
            container.appendChild(card);
        });
    }

    async function loadNotices() {
        try {
            noticesCache = await apiJson("/api/notices");
            renderNoticesTable();
            renderNoticesTimeline();
        } catch (err) {
            showToast(`Error loading notices: ${err.message}`, "error");
        }
    }

    function renderNoticesTable() {
        const tbody = document.getElementById("notices-table-body");
        if (!tbody) return;
        const query = ((document.getElementById("notices-search") || {}).value || "").trim().toLowerCase();
        const filtered = noticesCache.filter(n => {
            if (!query) return true;
            return `${n.title} ${n.banner_text} ${n.target_sites} ${n.target_groups}`.toLowerCase().includes(query);
        });

        tbody.innerHTML = "";
        if (!filtered.length) {
            const tr = document.createElement("tr");
            const td = document.createElement("td");
            td.colSpan = 7;
            td.style.textAlign = "center";
            td.className = "text-muted";
            td.textContent = "No matching notice banners.";
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        filtered.forEach(n => {
            const tr = document.createElement("tr");

            const tdMsg = document.createElement("td");
            tdMsg.style.maxWidth = "280px";
            if (n.title) {
                const strong = document.createElement("div");
                strong.style.fontWeight = "600";
                strong.textContent = n.title;
                tdMsg.appendChild(strong);
            }
            const snippet = document.createElement("div");
            snippet.className = "text-muted";
            snippet.style.fontSize = "0.8rem";
            snippet.textContent = n.banner_text || n.text || "";
            tdMsg.appendChild(snippet);

            const tdSched = document.createElement("td");
            const sSmall = document.createElement("small");
            sSmall.textContent = `S: ${new Date(n.scheduled_start).toLocaleString()} | E: ${new Date(n.scheduled_end).toLocaleString()}`;
            tdSched.appendChild(sSmall);

            const tdScope = document.createElement("td");
            if (n.target_type === "all") tdScope.textContent = "All Sites";
            else if (n.target_type === "groups") tdScope.textContent = `Groups: ${n.target_groups || "-"}`;
            else tdScope.textContent = `Targets: ${n.target_sites || "-"}`;

            const tdInt = document.createElement("td");
            const ints = [];
            if (n.send_smtp) ints.push(n.smtp_sent ? "📧 Sent" : "📧 Queued");
            if (n.uptime_kuma_integration) ints.push("🟢 Kuma");
            tdInt.textContent = ints.join(", ") || "None";

            const tdStats = document.createElement("td");
            tdStats.textContent = `${n.views_count || 0} / ${n.dismiss_count || 0} / ${n.clicks_count || 0}`;

            const tdStatus = document.createElement("td");
            const badge = document.createElement("span");
            const badgeClass = { active: "badge-success", scheduled: "badge-warning", paused: "badge-info", expired: "badge-muted" }[n.status] || "badge-muted";
            badge.className = `badge ${badgeClass}`;
            badge.textContent = n.status;
            tdStatus.appendChild(badge);

            const tdAct = document.createElement("td");
            tdAct.className = "text-right";
            tdAct.style.whiteSpace = "nowrap";

            const isEditor = !currentUser || currentUser.role === "admin" || currentUser.role === "editor";
            if (isEditor) {
                const emailBtn = document.createElement("button");
                emailBtn.type = "button";
                emailBtn.className = "btn btn-secondary btn-sm";
                emailBtn.textContent = "📧";
                emailBtn.title = "Send Email Campaign Now";
                emailBtn.addEventListener("click", () => sendNoticeEmailNow(n.id));

                const toggleBtn = document.createElement("button");
                toggleBtn.type = "button";
                toggleBtn.className = "btn btn-secondary btn-sm";
                toggleBtn.textContent = n.enabled ? "Pause" : "Resume";
                toggleBtn.style.marginLeft = "4px";
                toggleBtn.addEventListener("click", () => toggleNotice(n.id));

                const dupBtn = document.createElement("button");
                dupBtn.type = "button";
                dupBtn.className = "btn btn-secondary btn-sm";
                dupBtn.textContent = "Copy";
                dupBtn.style.marginLeft = "4px";
                dupBtn.addEventListener("click", () => duplicateNotice(n.id));

                const editBtn = document.createElement("button");
                editBtn.type = "button";
                editBtn.className = "btn btn-secondary btn-sm";
                editBtn.textContent = "Edit";
                editBtn.style.marginLeft = "4px";
                editBtn.addEventListener("click", () => editNotice(n));

                const delBtn = document.createElement("button");
                delBtn.type = "button";
                delBtn.className = "btn btn-danger btn-sm";
                delBtn.textContent = "Delete";
                delBtn.style.marginLeft = "4px";
                delBtn.addEventListener("click", () => deleteNotice(n.id));

                tdAct.append(emailBtn, toggleBtn, dupBtn, editBtn, delBtn);
            }
            tr.append(tdMsg, tdSched, tdScope, tdInt, tdStats, tdStatus, tdAct);
            tbody.appendChild(tr);
        });
    }

    async function sendNoticeEmailNow(id) {
        if (!confirm("Send email campaign for this notice to active matching subscribers now?")) return;
        try {
            const res = await apiJson(`/api/notices/${id}/send-email`, { method: "POST" });
            showToast(`Sent ${res.sent} email(s) (${res.failed} failed)`, res.failed && !res.sent ? "error" : "success");
            loadNotices();
        } catch (err) {
            showToast(err.message, "error");
        }
    }

    function editNotice(n) {
        document.getElementById("editor-action-title").textContent = `Edit Notice #${n.id}`;
        document.getElementById("notice-id").value = n.id;
        document.getElementById("notice-title").value = n.title || "";
        document.getElementById("notice-icon").value = n.icon || "";
        document.getElementById("notice-priority").value = n.priority ?? 10;
        document.getElementById("notice-text").value = n.banner_text || n.text || "";
        document.getElementById("notice-bg-color").value = n.bg_color || "#ef4444";
        document.getElementById("notice-text-color").value = n.text_color || "#ffffff";
        document.getElementById("notice-position").value = n.position || "top";
        document.getElementById("notice-cta-text").value = n.cta_text || "";
        document.getElementById("notice-cta-url").value = n.cta_url || "";
        document.getElementById("notice-cta-bg").value = n.cta_bg_color || "#ffffff";
        document.getElementById("notice-cta-text-color").value = n.cta_text_color || "#0f172a";
        document.getElementById("notice-scheduled-start").value = toLocalInputValue(n.scheduled_start);
        document.getElementById("notice-scheduled-end").value = toLocalInputValue(n.scheduled_end);
        document.getElementById("notice-display-duration").value = n.display_duration || 0;
        document.getElementById("notice-dismiss-cooldown").value = n.dismiss_cooldown || 24;
        document.getElementById("notice-recurrence").value = n.recurrence || "none";
        document.getElementById("notice-target-type").value = n.target_type || "all";
        document.getElementById("notice-target-sites").value = n.target_sites || "";
        document.getElementById("notice-target-groups").value = n.target_groups || "";
        document.getElementById("notice-exclude-sites").value = n.exclude_sites || "";
        document.getElementById("notice-target-paths").value = n.target_paths || "";
        document.getElementById("notice-audience").value = n.audience || "all";
        document.getElementById("notice-show-countdown").checked = Boolean(n.show_countdown);
        document.getElementById("notice-dismissible").checked = Boolean(n.dismissible);
        document.getElementById("notice-enabled").checked = Boolean(n.enabled);
        document.getElementById("notice-send-smtp").checked = Boolean(n.send_smtp);
        document.getElementById("notice-email-subject").value = n.email_subject || "";
        document.getElementById("notice-email-text").value = n.email_text || "";
        document.getElementById("notice-subscriber-tags").value = n.target_subscriber_tags || "";
        document.getElementById("notice-email-lead").value = String(n.email_lead_minutes || 0);
        document.getElementById("notice-smtp-settings").style.display = n.send_smtp ? "block" : "none";
        document.getElementById("notice-uptime-kuma-integration").checked = Boolean(n.uptime_kuma_integration);
        document.getElementById("notice-uptime-kuma-push-url").value = n.uptime_kuma_push_url || "";
        document.getElementById("notice-uptime-kuma-monitor-ids").value = n.uptime_kuma_monitor_ids || "";
        document.getElementById("notice-kuma-text").value = n.kuma_text || "";
        document.getElementById("notice-kuma-settings").style.display = n.uptime_kuma_integration ? "block" : "none";
        toggleTargetInputs(n.target_type || "all");
        syncBannerPreview();
        window.scrollTo({ top: 0, behavior: "smooth" });
    }

    window.saveNotice = async function (event) {
        event.preventDefault();
        const noticeId = document.getElementById("notice-id").value;
        const payload = {
            title: document.getElementById("notice-title").value,
            icon: document.getElementById("notice-icon").value,
            priority: parseInt(document.getElementById("notice-priority").value || "10", 10),
            banner_text: document.getElementById("notice-text").value,
            bg_color: document.getElementById("notice-bg-color").value,
            text_color: document.getElementById("notice-text-color").value,
            position: document.getElementById("notice-position").value,
            cta_text: document.getElementById("notice-cta-text").value,
            cta_url: document.getElementById("notice-cta-url").value,
            cta_bg_color: document.getElementById("notice-cta-bg").value,
            cta_text_color: document.getElementById("notice-cta-text-color").value,
            scheduled_start: fromLocalInputToUtcIso(document.getElementById("notice-scheduled-start").value),
            scheduled_end: fromLocalInputToUtcIso(document.getElementById("notice-scheduled-end").value),
            display_duration: parseInt(document.getElementById("notice-display-duration").value || "0", 10),
            dismiss_cooldown: parseInt(document.getElementById("notice-dismiss-cooldown").value || "24", 10),
            recurrence: document.getElementById("notice-recurrence").value || "none",
            target_type: document.getElementById("notice-target-type").value,
            target_sites: document.getElementById("notice-target-sites").value,
            target_groups: document.getElementById("notice-target-groups").value,
            exclude_sites: document.getElementById("notice-exclude-sites").value,
            target_paths: document.getElementById("notice-target-paths").value,
            audience: document.getElementById("notice-audience").value,
            show_countdown: document.getElementById("notice-show-countdown").checked,
            dismissible: document.getElementById("notice-dismissible").checked,
            enabled: document.getElementById("notice-enabled").checked,
            send_smtp: document.getElementById("notice-send-smtp").checked,
            email_subject: document.getElementById("notice-email-subject").value,
            email_text: document.getElementById("notice-email-text").value,
            target_subscriber_tags: document.getElementById("notice-subscriber-tags").value,
            email_lead_minutes: parseInt(document.getElementById("notice-email-lead").value || "0", 10),
            uptime_kuma_integration: document.getElementById("notice-uptime-kuma-integration").checked,
            uptime_kuma_push_url: document.getElementById("notice-uptime-kuma-push-url").value,
            uptime_kuma_monitor_ids: document.getElementById("notice-uptime-kuma-monitor-ids").value,
            kuma_text: document.getElementById("notice-kuma-text").value
        };

        try {
            const method = noticeId ? "PUT" : "POST";
            const endpoint = noticeId ? `/api/notices/${noticeId}` : "/api/notices";
            await apiJson(endpoint, { method, body: JSON.stringify(payload) });
            showToast("Notice banner saved!", "success");
            resetNoticeForm();
            loadNotices();
        } catch (err) {
            showToast(`Save failed: ${err.message}`, "error");
        }
    };

    async function toggleNotice(id) {
        try {
            await apiJson(`/api/notices/${id}/toggle`, { method: "POST" });
            showToast("Notice status updated", "success");
            loadNotices();
        } catch (err) {
            showToast(err.message, "error");
        }
    }

    async function duplicateNotice(id) {
        try {
            await apiJson(`/api/notices/${id}/duplicate`, { method: "POST" });
            showToast("Notice duplicated (paused)", "success");
            loadNotices();
        } catch (err) {
            showToast(err.message, "error");
        }
    }

    async function deleteNotice(id) {
        if (!confirm("Delete this notice banner?")) return;
        try {
            await apiJson(`/api/notices/${id}`, { method: "DELETE" });
            showToast("Notice deleted", "success");
            loadNotices();
        } catch (err) {
            showToast(err.message, "error");
        }
    }

    window.loadKumaMonitorPicker = async function () {
        const box = document.getElementById("kuma-monitor-picker");
        box.style.display = "flex";
        box.textContent = "Loading monitors from Uptime Kuma...";
        try {
            const data = await apiJson("/api/kuma/monitors");
            box.innerHTML = "";
            const currentIds = new Set(
                (document.getElementById("notice-uptime-kuma-monitor-ids").value || "")
                    .split(",").map(s => s.trim()).filter(Boolean)
            );
            (data.monitors || []).forEach(m => {
                const chip = document.createElement("label");
                chip.className = "monitor-chip";
                const cb = document.createElement("input");
                cb.type = "checkbox";
                cb.checked = currentIds.has(String(m.id));
                cb.addEventListener("change", () => {
                    if (cb.checked) currentIds.add(String(m.id));
                    else currentIds.delete(String(m.id));
                    document.getElementById("notice-uptime-kuma-monitor-ids").value = Array.from(currentIds).join(", ");
                });
                const txt = document.createElement("span");
                txt.textContent = `#${m.id} ${m.name}`;
                chip.append(cb, txt);
                box.appendChild(chip);
            });
            if (!data.monitors || !data.monitors.length) box.textContent = "No monitors returned.";
        } catch (err) {
            box.textContent = `Error: ${err.message}`;
        }
    };

    /* --- SITES & ONBOARDING --- */
    async function loadSites() {
        try {
            sitesCache = await apiJson("/api/sites");
            const tbody = document.getElementById("sites-table-body");
            if (!tbody) return;
            tbody.innerHTML = "";
            if (!sitesCache.length) {
                const tr = document.createElement("tr");
                const td = document.createElement("td");
                td.colSpan = 7;
                td.style.textAlign = "center";
                td.className = "text-muted";
                td.textContent = "No connected sites yet. Generate an onboarding link above.";
                tr.appendChild(td);
                tbody.appendChild(tr);
                return;
            }
            sitesCache.forEach(s => {
                const tr = document.createElement("tr");
                const tdName = document.createElement("td");
                const dot = document.createElement("span");
                dot.className = `site-dot ${s.online ? "online" : "offline"}`;
                const nameSpan = document.createElement("strong");
                nameSpan.textContent = s.name;
                tdName.append(dot, nameSpan);

                const tdDom = document.createElement("td");
                tdDom.textContent = s.domain;

                const tdGrp = document.createElement("td");
                tdGrp.textContent = s.groups || "-";

                const tdVer = document.createElement("td");
                tdVer.textContent = s.plugin_version ? `v${s.plugin_version} / WP ${s.wp_version || "?"}` : "Manual";
                if (s.plugin_version && s.update_available && s.latest_plugin_version) {
                    const upd = document.createElement("span");
                    upd.className = "badge badge-warning";
                    upd.style.marginLeft = "6px";
                    upd.textContent = `Update v${s.latest_plugin_version}`;
                    tdVer.appendChild(upd);
                }

                const tdSt = document.createElement("td");
                tdSt.textContent = `${s.stats.views} / ${s.stats.dismisses} / ${s.stats.clicks}`;

                const tdStatus = document.createElement("td");
                const badge = document.createElement("span");
                badge.className = `badge ${s.enabled ? "badge-success" : "badge-danger"}`;
                badge.textContent = s.enabled ? "Enabled" : "Muted";
                tdStatus.appendChild(badge);

                const tdAct = document.createElement("td");
                tdAct.className = "text-right";
                const cfgBtn = document.createElement("button");
                cfgBtn.type = "button";
                cfgBtn.className = "btn btn-secondary btn-sm";
                cfgBtn.textContent = "Controls";
                cfgBtn.addEventListener("click", () => openSiteModal(s));

                const delBtn = document.createElement("button");
                delBtn.type = "button";
                delBtn.className = "btn btn-danger btn-sm";
                delBtn.textContent = "Delete";
                delBtn.style.marginLeft = "4px";
                delBtn.addEventListener("click", () => deleteSite(s.id));

                tdAct.append(cfgBtn, delBtn);
                tr.append(tdName, tdDom, tdGrp, tdVer, tdSt, tdStatus, tdAct);
                tbody.appendChild(tr);
            });
        } catch (err) {}
    }

    window.generatePairingLink = async function (event) {
        event.preventDefault();
        try {
            const data = await apiJson("/api/sites/pairing-tokens", {
                method: "POST",
                body: JSON.stringify({
                    preset_name: document.getElementById("pair-site-name").value,
                    preset_groups: document.getElementById("pair-site-groups").value,
                    wp_url: document.getElementById("pair-wp-url").value
                })
            });
            document.getElementById("pairing-result-box").style.display = "block";
            document.getElementById("pairing-url-output").textContent = data.pairing_url;
            const deepBtn = document.getElementById("pairing-deep-link-btn");
            if (data.wp_deep_link) {
                deepBtn.href = data.wp_deep_link;
                deepBtn.style.display = "inline-flex";
            } else {
                deepBtn.style.display = "none";
            }
            showToast("Onboarding link created (valid 15 min)!", "success");
        } catch (err) {
            showToast(err.message, "error");
        }
    };

    window.createSiteManual = async function (event) {
        event.preventDefault();
        try {
            const res = await apiJson("/api/sites", {
                method: "POST",
                body: JSON.stringify({
                    name: document.getElementById("new-site-name").value,
                    domain: document.getElementById("new-site-domain").value,
                    groups: document.getElementById("new-site-groups").value
                })
            });
            document.getElementById("new-site-name").value = "";
            document.getElementById("new-site-domain").value = "";
            document.getElementById("new-site-groups").value = "";
            showToast(`Site registered (Key: ${res.site_key_prefix}...)`, "success");
            loadSites();
        } catch (err) {
            showToast(err.message, "error");
        }
    };

    function openSiteModal(s) {
        const cfg = s.settings || {};
        document.getElementById("site-modal-title").textContent = `Per-Site Controls: ${s.domain}`;
        document.getElementById("site-edit-id").value = s.id;
        document.getElementById("site-edit-name").value = s.name || "";
        document.getElementById("site-edit-domain").value = s.domain || "";
        document.getElementById("site-edit-groups").value = s.groups || "";
        document.getElementById("site-edit-url").value = s.site_url || "";
        document.getElementById("site-edit-enabled").checked = Boolean(s.enabled);
        document.getElementById("site-cfg-position").value = cfg.position || "inherit";
        document.getElementById("site-cfg-zindex").value = cfg.z_index || 999999;
        document.getElementById("site-cfg-max-banners").value = cfg.max_banners || 3;
        document.getElementById("site-cfg-mount").value = cfg.mount_selector || "";
        document.getElementById("site-cfg-audience").value = cfg.audience || "all";
        document.getElementById("site-cfg-include-paths").value = cfg.include_paths || "";
        document.getElementById("site-cfg-exclude-paths").value = cfg.exclude_paths || "";
        document.getElementById("site-cfg-roles").value = cfg.allowed_roles || "";
        document.getElementById("site-cfg-device").value = cfg.device || "all";
        document.getElementById("site-cfg-cache-ttl").value = cfg.cache_ttl || 30;
        document.getElementById("site-cfg-lock-local").checked = Boolean(cfg.lock_local_overrides);
        document.getElementById("site-cfg-css").value = cfg.custom_css || "";
        document.getElementById("site-cfg-inherit-font").checked = Boolean(cfg.inherit_font);
        document.getElementById("site-settings-modal").classList.add("open");
    }

    window.closeSiteModal = function () {
        document.getElementById("site-settings-modal").classList.remove("open");
    };

    window.saveSiteModal = async function (event) {
        event.preventDefault();
        const id = document.getElementById("site-edit-id").value;
        try {
            await apiJson(`/api/sites/${id}`, {
                method: "PUT",
                body: JSON.stringify({
                    name: document.getElementById("site-edit-name").value,
                    domain: document.getElementById("site-edit-domain").value,
                    groups: document.getElementById("site-edit-groups").value,
                    site_url: document.getElementById("site-edit-url").value,
                    enabled: document.getElementById("site-edit-enabled").checked,
                    settings: {
                        position: document.getElementById("site-cfg-position").value,
                        z_index: parseInt(document.getElementById("site-cfg-zindex").value || "999999", 10),
                        max_banners: parseInt(document.getElementById("site-cfg-max-banners").value || "3", 10),
                        mount_selector: document.getElementById("site-cfg-mount").value,
                        audience: document.getElementById("site-cfg-audience").value,
                        include_paths: document.getElementById("site-cfg-include-paths").value,
                        exclude_paths: document.getElementById("site-cfg-exclude-paths").value,
                        allowed_roles: document.getElementById("site-cfg-roles").value,
                        device: document.getElementById("site-cfg-device").value,
                        cache_ttl: parseInt(document.getElementById("site-cfg-cache-ttl").value || "30", 10),
                        lock_local_overrides: document.getElementById("site-cfg-lock-local").checked,
                        custom_css: document.getElementById("site-cfg-css").value,
                        inherit_font: document.getElementById("site-cfg-inherit-font").checked
                    }
                })
            });
            showToast("Site controls saved!", "success");
            closeSiteModal();
            loadSites();
        } catch (err) {
            showToast(err.message, "error");
        }
    };

    window.rotateCurrentSiteKey = async function () {
        const id = document.getElementById("site-edit-id").value;
        if (!confirm("Rotate this site's API key? Re-pair the WordPress site if needed.")) return;
        try {
            const res = await apiJson(`/api/sites/${id}/rotate-key`, { method: "POST" });
            document.getElementById("revealed-site-key-box").textContent = res.site_key;
            document.getElementById("site-key-modal").classList.add("open");
            loadSites();
        } catch (err) {
            showToast(err.message, "error");
        }
    };

    async function deleteSite(id) {
        if (!confirm("Delete this site from OmniBanner?")) return;
        try {
            await apiJson(`/api/sites/${id}`, { method: "DELETE" });
            showToast("Site deleted", "success");
            loadSites();
        } catch (err) {
            showToast(err.message, "error");
        }
    }

    /* --- SMTP & SUBSCRIBERS --- */
    async function loadSMTP() {
        try {
            const s = await apiJson("/api/smtp");
            document.getElementById("smtp-host").value = s.host || "";
            document.getElementById("smtp-port").value = s.port || 587;
            document.getElementById("smtp-tls-mode").value = s.tls_mode || "starttls";
            document.getElementById("smtp-username").value = s.username || "";
            document.getElementById("smtp-password").value = "";
            document.getElementById("smtp-pass-status").textContent = s.has_password ? "(saved)" : "";
            document.getElementById("smtp-from-email").value = s.from_email || "";
            document.getElementById("smtp-from-name").value = s.from_name || "OmniBanner Alerts";
            document.getElementById("smtp-enabled").checked = Boolean(s.enabled);
        } catch (e) {}
    }

    window.saveSMTP = async function (event) {
        event.preventDefault();
        try {
            await apiJson("/api/smtp", {
                method: "POST",
                body: JSON.stringify({
                    host: document.getElementById("smtp-host").value,
                    port: parseInt(document.getElementById("smtp-port").value || "587", 10),
                    tls_mode: document.getElementById("smtp-tls-mode").value,
                    username: document.getElementById("smtp-username").value,
                    password: document.getElementById("smtp-password").value,
                    from_email: document.getElementById("smtp-from-email").value,
                    from_name: document.getElementById("smtp-from-name").value,
                    enabled: document.getElementById("smtp-enabled").checked
                })
            });
            showToast("SMTP settings saved!", "success");
            loadSMTP();
        } catch (err) {
            showToast(err.message, "error");
        }
    };

    async function loadSubscribers() {
        try {
            const subs = await apiJson("/api/subscribers");
            const tbody = document.getElementById("subscribers-table-body");
            tbody.innerHTML = "";
            if (!subs.length) {
                const tr = document.createElement("tr");
                const td = document.createElement("td");
                td.colSpan = 4;
                td.style.textAlign = "center";
                td.className = "text-muted";
                td.textContent = "No active subscribers.";
                tr.appendChild(td);
                tbody.appendChild(tr);
                return;
            }
            subs.forEach(s => {
                const tr = document.createElement("tr");
                const tdEmail = document.createElement("td");
                tdEmail.textContent = s.email;
                const tdName = document.createElement("td");
                tdName.textContent = s.name || "-";
                const tdTags = document.createElement("td");
                tdTags.textContent = s.tags || "-";
                const tdAct = document.createElement("td");
                tdAct.className = "text-right";
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "btn btn-danger btn-sm";
                btn.textContent = "Remove";
                btn.addEventListener("click", () => deleteSubscriber(s.id));
                tdAct.appendChild(btn);
                tr.append(tdEmail, tdName, tdTags, tdAct);
                tbody.appendChild(tr);
            });
        } catch (e) {}
    }

    window.addSubscriber = async function (event) {
        event.preventDefault();
        try {
            await apiJson("/api/subscribers", {
                method: "POST",
                body: JSON.stringify({
                    email: document.getElementById("sub-email").value,
                    name: document.getElementById("sub-name").value,
                    tags: document.getElementById("sub-tags").value
                })
            });
            document.getElementById("sub-email").value = "";
            document.getElementById("sub-name").value = "";
            document.getElementById("sub-tags").value = "";
            showToast("Subscriber added!", "success");
            loadSubscribers();
        } catch (err) {
            showToast(err.message, "error");
        }
    };

    async function deleteSubscriber(id) {
        if (!confirm("Remove subscriber?")) return;
        try {
            await apiJson(`/api/subscribers/${id}`, { method: "DELETE" });
            showToast("Subscriber removed", "success");
            loadSubscribers();
        } catch (err) {
            showToast(err.message, "error");
        }
    }

    window.importSubscribersCSV = async function (input) {
        const file = input.files && input.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append("file", file);
        try {
            const resp = await fetch("/api/subscribers/import", { method: "POST", body: formData });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || "Import failed");
            showToast(`Imported ${data.count} subscriber(s)!`, "success");
            loadSubscribers();
        } catch (err) {
            showToast(err.message, "error");
        } finally {
            input.value = "";
        }
    };

    /* --- INTEGRATIONS (KUMA, WEBHOOKS, INBOUND) --- */
    async function loadKumaSettings() {
        try {
            const k = await apiJson("/api/kuma");
            document.getElementById("kuma-url").value = k.url || "";
            document.getElementById("kuma-username").value = k.username || "";
            document.getElementById("kuma-password").value = "";
            document.getElementById("kuma-pass-status").textContent = k.has_password ? "(saved)" : "";
            document.getElementById("kuma-api-mode").value = k.api_mode || "maintenance";
            document.getElementById("kuma-verify-tls").checked = k.verify_tls !== false;
            document.getElementById("kuma-enabled").checked = Boolean(k.enabled);
        } catch (e) {}
    }

    window.saveKumaSettings = async function (event) {
        event.preventDefault();
        try {
            await apiJson("/api/kuma", {
                method: "POST",
                body: JSON.stringify({
                    url: document.getElementById("kuma-url").value,
                    username: document.getElementById("kuma-username").value,
                    password: document.getElementById("kuma-password").value,
                    api_mode: document.getElementById("kuma-api-mode").value,
                    verify_tls: document.getElementById("kuma-verify-tls").checked,
                    enabled: document.getElementById("kuma-enabled").checked
                })
            });
            showToast("Uptime Kuma settings saved!", "success");
            loadKumaSettings();
        } catch (err) {
            showToast(err.message, "error");
        }
    };

    async function loadWebhooks() {
        try {
            const hooks = await apiJson("/api/webhooks");
            const tbody = document.getElementById("webhooks-table-body");
            tbody.innerHTML = "";
            hooks.forEach(w => {
                const tr = document.createElement("tr");
                const tdName = document.createElement("td"); tdName.textContent = w.name;
                const tdKind = document.createElement("td"); tdKind.textContent = w.kind;
                const tdSt = document.createElement("td"); tdSt.textContent = w.last_status || "Ready";
                const tdAct = document.createElement("td"); tdAct.className = "text-right";
                const testBtn = document.createElement("button");
                testBtn.type = "button"; testBtn.className = "btn btn-secondary btn-sm"; testBtn.textContent = "Test";
                testBtn.addEventListener("click", async () => {
                    try {
                        const r = await apiJson(`/api/webhooks/${w.id}/test`, { method: "POST" });
                        showToast(r.success ? "Webhook test sent!" : `Failed: ${r.detail}`, r.success ? "success" : "error");
                        loadWebhooks();
                    } catch (e) { showToast(e.message, "error"); }
                });
                const delBtn = document.createElement("button");
                delBtn.type = "button"; delBtn.className = "btn btn-danger btn-sm"; delBtn.textContent = "Delete"; delBtn.style.marginLeft = "4px";
                delBtn.addEventListener("click", async () => {
                    await apiJson(`/api/webhooks/${w.id}`, { method: "DELETE" });
                    loadWebhooks();
                });
                tdAct.append(testBtn, delBtn);
                tr.append(tdName, tdKind, tdSt, tdAct);
                tbody.appendChild(tr);
            });
        } catch (e) {}
    }

    window.createWebhook = async function (event) {
        event.preventDefault();
        try {
            await apiJson("/api/webhooks", {
                method: "POST",
                body: JSON.stringify({
                    name: document.getElementById("wh-name").value,
                    kind: document.getElementById("wh-kind").value,
                    url: document.getElementById("wh-url").value,
                    secret: document.getElementById("wh-secret").value,
                    events: document.getElementById("wh-events").value
                })
            });
            event.target.reset();
            showToast("Webhook added!", "success");
            loadWebhooks();
        } catch (err) { showToast(err.message, "error"); }
    };

    async function loadInboundRules() {
        try {
            const rules = await apiJson("/api/inbound-rules");
            const tbody = document.getElementById("inbound-rules-table-body");
            tbody.innerHTML = "";
            rules.forEach(r => {
                const tr = document.createElement("tr");
                const tdN = document.createElement("td"); tdN.textContent = r.name;
                const tdP = document.createElement("td"); tdP.textContent = r.token_prefix + "...";
                const tdT = document.createElement("td"); tdT.textContent = r.banner_template_text;
                const tdG = document.createElement("td"); tdG.textContent = r.target_groups || r.target_type;
                const tdA = document.createElement("td"); tdA.className = "text-right";
                const del = document.createElement("button");
                del.type = "button"; del.className = "btn btn-danger btn-sm"; del.textContent = "Delete";
                del.addEventListener("click", async () => {
                    await apiJson(`/api/inbound-rules/${r.id}`, { method: "DELETE" });
                    loadInboundRules();
                });
                tdA.appendChild(del);
                tr.append(tdN, tdP, tdT, tdG, tdA);
                tbody.appendChild(tr);
            });
        } catch (e) {}
    }

    window.createInboundRule = async function (event) {
        event.preventDefault();
        try {
            const res = await apiJson("/api/inbound-rules", {
                method: "POST",
                body: JSON.stringify({
                    name: document.getElementById("inb-name").value,
                    banner_template_text: document.getElementById("inb-template").value,
                    target_type: document.getElementById("inb-groups").value.trim() ? "groups" : "all",
                    target_groups: document.getElementById("inb-groups").value.trim()
                })
            });
            const box = document.getElementById("inbound-new-url-box");
            box.style.display = "block";
            box.textContent = `Inbound Webhook URL (copy now): ${res.webhook_url}`;
            event.target.reset();
            loadInboundRules();
        } catch (err) { showToast(err.message, "error"); }
    };

    /* --- AUTH, OIDC, USERS, AUDIT --- */
    async function loadOIDCProviders() {
        try {
            oidcCache = await apiJson("/api/oidc/providers");
            const tbody = document.getElementById("oidc-providers-table-body");
            tbody.innerHTML = "";
            oidcCache.forEach(p => {
                const tr = document.createElement("tr");
                const tdN = document.createElement("td"); tdN.textContent = p.name;
                const tdS = document.createElement("td"); tdS.textContent = p.slug;
                const tdE = document.createElement("td");
                const badge = document.createElement("span");
                badge.className = `badge ${p.enabled ? "badge-success" : "badge-muted"}`;
                badge.textContent = p.enabled ? "Enabled" : "Disabled";
                tdE.appendChild(badge);
                const tdA = document.createElement("td"); tdA.className = "text-right";
                const editBtn = document.createElement("button");
                editBtn.type = "button"; editBtn.className = "btn btn-secondary btn-sm"; editBtn.textContent = "Edit";
                editBtn.addEventListener("click", () => {
                    document.getElementById("oidc-edit-id").value = p.id;
                    document.getElementById("oidc-name").value = p.name;
                    document.getElementById("oidc-slug").value = p.slug;
                    document.getElementById("oidc-issuer").value = p.issuer_url;
                    document.getElementById("oidc-client-id").value = p.client_id;
                    document.getElementById("oidc-client-secret").value = "";
                    document.getElementById("oidc-secret-hint").textContent = p.has_secret ? "(saved)" : "";
                    document.getElementById("oidc-scopes").value = p.scopes;
                    document.getElementById("oidc-groups-claim").value = p.groups_claim;
                    document.getElementById("oidc-admin-groups").value = p.admin_groups;
                    document.getElementById("oidc-editor-groups").value = p.editor_groups;
                    document.getElementById("oidc-viewer-groups").value = p.viewer_groups;
                    document.getElementById("oidc-require-group").checked = p.require_group_match;
                    document.getElementById("oidc-verify-tls").checked = p.verify_tls;
                    document.getElementById("oidc-end-session-logout").checked = Boolean(p.end_session_logout);
                    document.getElementById("oidc-slug").dispatchEvent(new Event("input"));
                });
                const delBtn = document.createElement("button");
                delBtn.type = "button"; delBtn.className = "btn btn-danger btn-sm"; delBtn.textContent = "Delete"; delBtn.style.marginLeft = "4px";
                delBtn.addEventListener("click", async () => {
                    if (!confirm("Delete this OIDC provider?")) return;
                    await apiJson(`/api/oidc/providers/${p.id}`, { method: "DELETE" });
                    loadOIDCProviders();
                });
                tdA.append(editBtn, delBtn);
                tr.append(tdN, tdS, tdE, tdA);
                tbody.appendChild(tr);
            });
        } catch (e) {}
    }

    window.resetOIDCForm = function () {
        document.getElementById("oidc-provider-form").reset();
        document.getElementById("oidc-edit-id").value = "";
        document.getElementById("oidc-secret-hint").textContent = "";
    };

    window.saveOIDCProvider = async function (event) {
        event.preventDefault();
        const id = document.getElementById("oidc-edit-id").value;
        const payload = {
            name: document.getElementById("oidc-name").value,
            slug: document.getElementById("oidc-slug").value,
            issuer_url: document.getElementById("oidc-issuer").value,
            client_id: document.getElementById("oidc-client-id").value,
            client_secret: document.getElementById("oidc-client-secret").value,
            scopes: document.getElementById("oidc-scopes").value,
            groups_claim: document.getElementById("oidc-groups-claim").value,
            admin_groups: document.getElementById("oidc-admin-groups").value,
            editor_groups: document.getElementById("oidc-editor-groups").value,
            viewer_groups: document.getElementById("oidc-viewer-groups").value,
            require_group_match: document.getElementById("oidc-require-group").checked,
            verify_tls: document.getElementById("oidc-verify-tls").checked,
            end_session_logout: document.getElementById("oidc-end-session-logout").checked,
            enabled: true
        };
        try {
            await apiJson(id ? `/api/oidc/providers/${id}` : "/api/oidc/providers", {
                method: id ? "PUT" : "POST",
                body: JSON.stringify(payload)
            });
            showToast("OIDC provider saved!", "success");
            resetOIDCForm();
            loadOIDCProviders();
        } catch (err) { showToast(err.message, "error"); }
    };

    window.testDashboardOIDC = async function () {
        const issuer = document.getElementById("oidc-issuer").value.trim();
        const verify_tls = document.getElementById("oidc-verify-tls").checked;
        if (!issuer) return showToast("Enter Issuer URL first.", "error");
        try {
            const res = await apiJson("/api/oidc/test", {
                method: "POST",
                body: JSON.stringify({ issuer_url: issuer, verify_tls })
            });
            if (res.success) showToast(`Discovery OK! ${res.jwks_key_count} JWKS key(s) loaded.`, "success");
            else showToast(res.detail || "OIDC test failed", "error");
        } catch (err) { showToast(err.message, "error"); }
    };

    async function loadSecurity() {
        try {
            const status = await apiJson("/api/security/status");
            const label = document.getElementById("mfa-status-label");
            const btn = document.getElementById("totp-setup-btn");
            const acctLabel = document.getElementById("acct-mfa-label");
            const acctBtn = document.getElementById("acct-mfa-btn");
            if (status.totp_enabled) {
                if (label) { label.textContent = "Enabled"; label.style.color = "var(--success)"; }
                if (btn) { btn.textContent = "Disable MFA"; btn.className = "btn btn-danger"; }
                if (acctLabel) { acctLabel.textContent = "Enabled"; acctLabel.style.color = "var(--success)"; }
                if (acctBtn) { acctBtn.textContent = "Disable MFA"; acctBtn.className = "btn btn-danger btn-sm"; }
            } else {
                if (label) { label.textContent = "Disabled"; label.style.color = "var(--danger)"; }
                if (btn) { btn.textContent = "Setup MFA"; btn.className = "btn btn-secondary"; }
                if (acctLabel) { acctLabel.textContent = "Disabled"; acctLabel.style.color = "var(--danger)"; }
                if (acctBtn) { acctBtn.textContent = "Setup MFA"; acctBtn.className = "btn btn-secondary btn-sm"; }
            }
        } catch (e) {}
    }

    window.toggleTOTPStatus = function () {
        const label = document.getElementById("mfa-status-label") || document.getElementById("acct-mfa-label");
        if (label && label.textContent.trim() === "Enabled") {
            document.getElementById("disable-mfa-password").value = "";
            document.getElementById("disable-mfa-code").value = "";
            document.getElementById("disable-mfa-modal").classList.add("open");
        } else {
            window.location.href = "/totp-setup";
        }
    };

    window.confirmDisableMFA = async function (event) {
        event.preventDefault();
        const password = document.getElementById("disable-mfa-password").value;
        const code = document.getElementById("disable-mfa-code").value.trim();
        if (!password && !code) {
            return showToast("Enter your password or MFA code.", "error");
        }
        try {
            await apiJson("/api/totp/disable", { method: "POST", body: JSON.stringify({ password, code }) });
            closeModal("disable-mfa-modal");
            showToast("MFA disabled.", "success");
            loadSecurity();
        } catch (err) { showToast(err.message, "error"); }
    };

    window.openAccountModal = function () {
        if (currentUser) {
            document.getElementById("acct-username").textContent = currentUser.username;
            document.getElementById("acct-role").textContent = currentUser.role;
            document.getElementById("change-password-form").style.display = currentUser.is_oidc ? "none" : "block";
            document.getElementById("acct-oidc-note").style.display = currentUser.is_oidc ? "block" : "none";
        }
        document.getElementById("account-modal").classList.add("open");
    };

    window.submitChangePassword = async function (event) {
        event.preventDefault();
        try {
            await apiJson("/api/me/password", {
                method: "POST",
                body: JSON.stringify({
                    current_password: document.getElementById("acct-cur-pass").value,
                    new_password: document.getElementById("acct-new-pass").value
                })
            });
            event.target.reset();
            closeModal("account-modal");
            showToast("Password updated!", "success");
        } catch (err) { showToast(err.message, "error"); }
    };

    async function loadUsers() {
        try {
            const users = await apiJson("/api/users");
            const tbody = document.getElementById("users-table-body");
            tbody.innerHTML = "";
            users.forEach(u => {
                const tr = document.createElement("tr");
                const tdU = document.createElement("td"); tdU.textContent = u.username;
                const tdR = document.createElement("td"); tdR.textContent = u.role;
                const tdT = document.createElement("td"); tdT.textContent = u.is_oidc ? "OIDC" : "Local";
                const tdA = document.createElement("td"); tdA.className = "text-right";
                const del = document.createElement("button");
                del.type = "button"; del.className = "btn btn-danger btn-sm"; del.textContent = "Remove";
                del.addEventListener("click", async () => {
                    if (!confirm(`Delete user ${u.username}?`)) return;
                    try {
                        await apiJson(`/api/users/${u.id}`, { method: "DELETE" });
                        loadUsers();
                    } catch (err) { showToast(err.message, "error"); }
                });
                tdA.appendChild(del);
                tr.append(tdU, tdR, tdT, tdA);
                tbody.appendChild(tr);
            });
        } catch (e) {}
    }

    window.createUser = async function (event) {
        event.preventDefault();
        try {
            await apiJson("/api/users", {
                method: "POST",
                body: JSON.stringify({
                    username: document.getElementById("new-user-name").value,
                    password: document.getElementById("new-user-pass").value,
                    role: document.getElementById("new-user-role").value
                })
            });
            event.target.reset();
            showToast("User created!", "success");
            loadUsers();
        } catch (err) { showToast(err.message, "error"); }
    };

    window.saveAuthPolicies = async function () {
        try {
            brandCache = await apiJson("/api/brand", {
                method: "POST",
                body: JSON.stringify({
                    hide_local_login: document.getElementById("policy-hide-local").checked,
                    allow_proxy_auth: document.getElementById("policy-proxy-auth").checked,
                    proxy_auth_header: document.getElementById("policy-proxy-header").value,
                    proxy_auth_groups_header: document.getElementById("policy-proxy-groups-hdr").value,
                    trusted_proxy_ips: document.getElementById("policy-trusted-proxies").value,
                    proxy_admin_groups: document.getElementById("policy-proxy-admin-groups").value,
                    proxy_editor_groups: document.getElementById("policy-proxy-editor-groups").value
                })
            });
            showToast("Authentication policy updated!", "success");
        } catch (err) { showToast(err.message, "error"); }
    };

    window.loadAuditLogs = async function () {
        try {
            const logs = await apiJson("/api/audit-logs?limit=50");
            const tbody = document.getElementById("audit-logs-body");
            tbody.innerHTML = "";
            logs.forEach(l => {
                const tr = document.createElement("tr");
                [l.timestamp, l.username, l.action, `${l.resource_type}:${l.resource_id}`, l.details, l.ip_address].forEach(val => {
                    const td = document.createElement("td");
                    td.textContent = val || "";
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });
        } catch (e) {}
    };

    /* --- BRANDING --- */
    async function loadBranding() {
        try {
            brandCache = await apiJson("/api/brand");
            document.getElementById("brand-app-name").value = brandCache.app_name || "OmniBanner";
            document.getElementById("brand-default-theme").value = brandCache.default_theme || "dark";
            document.getElementById("brand-primary-color").value = brandCache.primary_color || "#8b5cf6";
            document.getElementById("brand-secondary-color").value = brandCache.secondary_color || "#6366f1";
            document.getElementById("brand-public-url").value = brandCache.public_base_url || "";
            document.getElementById("brand-app-icon-url").value = brandCache.app_icon_url || "/static/brand/logo.png";
            document.getElementById("brand-favicon-url").value = brandCache.favicon_url || "/static/brand/logo.png";
            document.getElementById("brand-login-subtitle").value = brandCache.login_subtitle || "";
            document.getElementById("brand-footer-text").value = brandCache.footer_text || "";
            document.getElementById("brand-custom-css").value = brandCache.custom_css || "";
            const statusToggle = document.getElementById("brand-public-status-page");
            if (statusToggle) statusToggle.checked = brandCache.public_status_page !== false;

            const hideEl = document.getElementById("policy-hide-local");
            const proxyEl = document.getElementById("policy-proxy-auth");
            const hdrEl = document.getElementById("policy-proxy-header");
            const grpHdrEl = document.getElementById("policy-proxy-groups-hdr");
            const trustEl = document.getElementById("policy-trusted-proxies");
            const admGrpEl = document.getElementById("policy-proxy-admin-groups");
            const edGrpEl = document.getElementById("policy-proxy-editor-groups");
            if (hideEl) hideEl.checked = Boolean(brandCache.hide_local_login);
            if (proxyEl) proxyEl.checked = Boolean(brandCache.allow_proxy_auth);
            if (hdrEl) hdrEl.value = brandCache.proxy_auth_header || "X-authentik-username";
            if (grpHdrEl) grpHdrEl.value = brandCache.proxy_auth_groups_header || "X-authentik-groups";
            if (trustEl) trustEl.value = brandCache.trusted_proxy_ips || "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16";
            if (admGrpEl) admGrpEl.value = brandCache.proxy_admin_groups || "";
            if (edGrpEl) edGrpEl.value = brandCache.proxy_editor_groups || "";

            const slugEl = document.getElementById("oidc-slug");
            if (slugEl) slugEl.dispatchEvent(new Event("input"));
        } catch (e) {}
    }

    window.saveBranding = async function (event) {
        event.preventDefault();
        try {
            await apiJson("/api/brand", {
                method: "POST",
                body: JSON.stringify({
                    app_name: document.getElementById("brand-app-name").value,
                    default_theme: document.getElementById("brand-default-theme").value,
                    primary_color: document.getElementById("brand-primary-color").value,
                    secondary_color: document.getElementById("brand-secondary-color").value,
                    public_base_url: document.getElementById("brand-public-url").value,
                    app_icon_url: document.getElementById("brand-app-icon-url").value,
                    favicon_url: document.getElementById("brand-favicon-url").value,
                    login_subtitle: document.getElementById("brand-login-subtitle").value,
                    footer_text: document.getElementById("brand-footer-text").value,
                    custom_css: document.getElementById("brand-custom-css").value,
                    public_status_page: document.getElementById("brand-public-status-page").checked
                })
            });
            showToast("Branding saved! Reloading...", "success");
            setTimeout(() => window.location.reload(), 700);
        } catch (err) { showToast(err.message, "error"); }
    };

    window.importBackupFile = async function (input) {
        const file = input.files && input.files[0];
        if (!file) return;
        try {
            const text = await file.text();
            const json = JSON.parse(text);
            const res = await apiJson("/api/backup/import", { method: "POST", body: JSON.stringify(json) });
            const r = res.restored || {};
            showToast(`Restored ${r.templates || 0} templates, ${r.sites || 0} sites, ${r.subscribers || 0} subscribers`, "success");
            loadBranding();
            loadTemplates();
            loadSites();
            loadSubscribers();
        } catch (err) {
            showToast(`Backup import failed: ${err.message}`, "error");
        } finally {
            input.value = "";
        }
    };

    window.uploadBrandImage = async function (input, kind) {
        const file = input.files && input.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append("file", file);
        try {
            const resp = await fetch(`/api/brand/${kind}`, { method: "POST", body: formData });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || "Upload failed");
            if (kind === "logo") document.getElementById("brand-app-icon-url").value = data.url;
            else document.getElementById("brand-favicon-url").value = data.url;
            showToast(`${kind} uploaded!`, "success");
        } catch (err) {
            showToast(err.message, "error");
        } finally {
            input.value = "";
        }
    };
})();

