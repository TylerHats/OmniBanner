(function () {
    "use strict";

    if (typeof OmniBannerConf === "undefined" || !OmniBannerConf.api_url) {
        return;
    }

    const apiUrl = String(OmniBannerConf.api_url).replace(/\/$/, "");
    const domain = String(OmniBannerConf.domain || window.location.hostname || "");
    const siteId = OmniBannerConf.site_id ? Number(OmniBannerConf.site_id) : 0;
    const localSiteConfig = OmniBannerConf.site_config || {};
    const isLoggedIn = Boolean(OmniBannerConf.is_logged_in);
    const userRoles = Array.isArray(OmniBannerConf.user_roles) ? OmniBannerConf.user_roles : [];

    const ALLOWED_TAGS = new Set(["A", "STRONG", "B", "EM", "I", "U", "S", "CODE", "SPAN", "BR"]);

    function isSafeColor(val, fallback) {
        return typeof val === "string" && /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(val.trim()) ? val.trim() : fallback;
    }

    function isSafeUrl(url) {
        if (typeof url !== "string") return false;
        const cleaned = url.replace(/[\x00-\x20\x7f]+/g, "").trim();
        return /^(https?:\/\/|mailto:|\/(?!\/))/i.test(cleaned);
    }

    function sanitizeToFragment(rawHtml) {
        const frag = document.createDocumentFragment();
        if (!rawHtml) return frag;
        const doc = new DOMParser().parseFromString(String(rawHtml), "text/html");

        function walk(node, target) {
            for (const child of Array.from(node.childNodes)) {
                if (child.nodeType === Node.TEXT_NODE) {
                    target.appendChild(document.createTextNode(child.textContent));
                } else if (child.nodeType === Node.ELEMENT_NODE) {
                    const tag = child.tagName.toUpperCase();
                    if (tag === "SCRIPT" || tag === "STYLE" || tag === "IFRAME" || tag === "OBJECT" || tag === "EMBED" || tag === "SVG") {
                        continue;
                    }
                    if (!ALLOWED_TAGS.has(tag)) {
                        walk(child, target);
                        continue;
                    }
                    const el = document.createElement(tag.toLowerCase());
                    if (tag === "A") {
                        const href = child.getAttribute("href") || "";
                        if (isSafeUrl(href)) {
                            el.setAttribute("href", href);
                            if (/^https?:\/\//i.test(href)) {
                                el.setAttribute("target", "_blank");
                            }
                            el.setAttribute("rel", "noopener noreferrer");
                        }
                        const title = child.getAttribute("title");
                        if (title) {
                            el.setAttribute("title", title);
                        }
                    }
                    walk(child, el);
                    target.appendChild(el);
                }
            }
        }
        walk(doc.body, frag);
        return frag;
    }

    function matchGlobs(patternsCsv, path) {
        if (!patternsCsv) return false;
        const list = String(patternsCsv).split(",").map(function (s) { return s.trim(); }).filter(Boolean);
        return list.some(function (pattern) {
            const regexStr = "^" + pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$";
            return new RegExp(regexStr, "i").test(path);
        });
    }

    function isDismissed(bannerId) {
        const key = "omnibanner_dismissed_" + bannerId;
        const raw = localStorage.getItem(key);
        if (!raw) return false;
        if (Date.now() < parseInt(raw, 10)) return true;
        localStorage.removeItem(key);
        return false;
    }

    function sendEvent(bannerId, eventType) {
        const url = apiUrl + "/api/public/banner/" + encodeURIComponent(bannerId) + "/event";
        const payload = JSON.stringify({
            event: eventType,
            domain: domain,
            site_id: siteId || null
        });
        if (typeof fetch === "function") {
            fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: payload,
                mode: "cors",
                credentials: "omit",
                keepalive: true
            }).catch(function () {});
            return;
        }
        try {
            if (navigator.sendBeacon) {
                navigator.sendBeacon(url, payload);
            }
        } catch (e) {}
    }

    function formatRemaining(endIso) {
        const diff = Math.max(0, new Date(endIso).getTime() - Date.now());
        if (diff <= 0) return "Ended";
        const secs = Math.floor(diff / 1000);
        const d = Math.floor(secs / 86400);
        const h = Math.floor((secs % 86400) / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        return (d > 0 ? d + "d " : "") +
            String(h).padStart(2, "0") + ":" +
            String(m).padStart(2, "0") + ":" +
            String(s).padStart(2, "0");
    }

    function getAdminBarHeight() {
        const bar = document.getElementById("wpadminbar");
        return bar ? bar.getBoundingClientRect().height : 0;
    }

    function getOrCreateStack(position, siteConfig) {
        const cls = {
            "top-fixed": "omnibanner-stack-top-fixed",
            "bottom-fixed": "omnibanner-stack-bottom-fixed",
            "floating-br": "omnibanner-stack-floating-br",
            "floating-bl": "omnibanner-stack-floating-bl"
        }[position];
        if (!cls) return null;

        let container = document.querySelector("." + cls);
        if (!container) {
            container = document.createElement("div");
            container.className = cls;
            if (position === "top-fixed") {
                const offset = getAdminBarHeight();
                if (offset > 0) container.style.top = offset + "px";
            }
            if (siteConfig && siteConfig.z_index) {
                container.style.zIndex = String(siteConfig.z_index);
            }
            document.body.appendChild(container);
        }
        return container;
    }

    function renderBanner(data, siteConfig) {
        const banner = document.createElement("div");
        banner.className = "omnibanner-notice";
        banner.id = "omnibanner-" + data.id;
        banner.style.backgroundColor = isSafeColor(data.bg_color, "#ef4444");
        banner.style.color = isSafeColor(data.text_color, "#ffffff");
        if (siteConfig && siteConfig.inherit_font) {
            banner.style.fontFamily = "inherit";
        }
        if (siteConfig && siteConfig.z_index) {
            banner.style.zIndex = String(siteConfig.z_index);
        }

        if (data.icon) {
            const iconSpan = document.createElement("span");
            iconSpan.className = "omnibanner-icon";
            iconSpan.textContent = data.icon;
            banner.appendChild(iconSpan);
        }

        const msgSpan = document.createElement("span");
        msgSpan.className = "omnibanner-text";
        msgSpan.appendChild(sanitizeToFragment(data.text || data.banner_text || ""));
        banner.appendChild(msgSpan);

        if (data.show_countdown && data.scheduled_end) {
            const cd = document.createElement("span");
            cd.className = "omnibanner-countdown";
            cd.textContent = formatRemaining(data.scheduled_end);
            banner.appendChild(cd);
            const timer = setInterval(function () {
                if (!document.body.contains(cd)) {
                    clearInterval(timer);
                    return;
                }
                cd.textContent = formatRemaining(data.scheduled_end);
            }, 1000);
        }

        if (data.cta_text && data.cta_url && isSafeUrl(data.cta_url)) {
            const cta = document.createElement("a");
            cta.className = "omnibanner-cta-btn";
            cta.href = data.cta_url;
            cta.textContent = data.cta_text;
            cta.style.backgroundColor = isSafeColor(data.cta_bg_color, "#ffffff");
            cta.style.color = isSafeColor(data.cta_text_color, "#0f172a");
            if (data.cta_new_tab !== false && /^https?:\/\//i.test(data.cta_url)) {
                cta.target = "_blank";
                cta.rel = "noopener noreferrer";
            }
            cta.addEventListener("click", function () {
                sendEvent(data.id, "click");
            });
            banner.appendChild(cta);
        }

        if (data.dismissible) {
            const closeBtn = document.createElement("button");
            closeBtn.type = "button";
            closeBtn.className = "omnibanner-close-btn";
            closeBtn.textContent = "\u00D7";
            closeBtn.setAttribute("aria-label", "Dismiss banner");
            closeBtn.addEventListener("click", function (e) {
                e.preventDefault();
                const hours = Number(data.dismiss_cooldown || 24);
                localStorage.setItem("omnibanner_dismissed_" + data.id, String(Date.now() + hours * 3600 * 1000));
                sendEvent(data.id, "dismiss");
                banner.classList.add("omnibanner-fade-out");
                setTimeout(function () { banner.remove(); }, 310);
            });
            banner.appendChild(closeBtn);
        }

        const effectivePos = (siteConfig && siteConfig.position && siteConfig.position !== "inherit")
            ? siteConfig.position
            : (data.position || "top");

        const stack = getOrCreateStack(effectivePos, siteConfig);
        if (stack) {
            stack.appendChild(banner);
        } else {
            const mountSel = (siteConfig && siteConfig.mount_selector) || "";
            const mountTarget = mountSel ? document.querySelector(mountSel) : null;
            if (mountTarget) {
                mountTarget.insertBefore(banner, mountTarget.firstChild);
            } else {
                const adminBar = document.getElementById("wpadminbar");
                if (adminBar && adminBar.parentNode === document.body) {
                    adminBar.insertAdjacentElement("afterend", banner);
                } else {
                    document.body.insertBefore(banner, document.body.firstChild);
                }
            }
        }

        sendEvent(data.id, "view");

        if (Number(data.display_duration) > 0) {
            setTimeout(function () {
                banner.classList.add("omnibanner-fade-out");
                setTimeout(function () { banner.remove(); }, 310);
            }, Number(data.display_duration) * 1000);
        }
    }

    function boot() {
        const params = new URLSearchParams({
            domain: domain,
            path: window.location.pathname || "/",
            audience: isLoggedIn ? "logged_in" : "guest"
        });
        if (siteId > 0) {
            params.set("site_id", String(siteId));
        }

        fetch(apiUrl + "/api/public/banners?" + params.toString(), { mode: "cors" })
            .then(function (resp) {
                return resp.ok ? resp.json() : null;
            })
            .then(function (payload) {
                if (!payload || !Array.isArray(payload.banners) || payload.banners.length === 0) {
                    return;
                }
                const siteConfig = Object.assign({}, localSiteConfig, payload.site_config || {});
                if (siteConfig.enabled === false) {
                    return;
                }

                const path = window.location.pathname || "/";
                if (siteConfig.include_paths && !matchGlobs(siteConfig.include_paths, path)) return;
                if (siteConfig.exclude_paths && matchGlobs(siteConfig.exclude_paths, path)) return;

                if (siteConfig.device === "mobile" && window.innerWidth >= 768) return;
                if (siteConfig.device === "desktop" && window.innerWidth < 768) return;

                if (siteConfig.allowed_roles && String(siteConfig.allowed_roles).trim() !== "") {
                    if (!isLoggedIn) return;
                    const allowedSet = new Set(
                        String(siteConfig.allowed_roles).split(",").map(function (r) { return r.trim().toLowerCase(); }).filter(Boolean)
                    );
                    const hasRole = userRoles.some(function (r) { return allowedSet.has(String(r).toLowerCase()); });
                    if (!hasRole) return;
                }

                if (siteConfig.custom_css && !document.getElementById("omnibanner-site-custom-css")) {
                    const styleEl = document.createElement("style");
                    styleEl.id = "omnibanner-site-custom-css";
                    styleEl.textContent = String(siteConfig.custom_css);
                    document.head.appendChild(styleEl);
                }

                const maxBanners = Number(siteConfig.max_banners || 3);
                let shown = 0;
                for (const b of payload.banners) {
                    if (shown >= maxBanners) break;
                    if (isDismissed(b.id)) continue;
                    renderBanner(b, siteConfig);
                    shown += 1;
                }
            })
            .catch(function (err) {
                console.warn("OmniBanner notice fetch error:", err);
            });
    }

    if (document.body) {
        boot();
    } else {
        document.addEventListener("DOMContentLoaded", boot);
    }
})();
