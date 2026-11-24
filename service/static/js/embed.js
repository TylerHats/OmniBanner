(function () {
    "use strict";

    const currentScript = document.currentScript || (function () {
        const scripts = document.getElementsByTagName("script");
        return scripts[scripts.length - 1];
    })();

    let apiUrl = (currentScript && currentScript.getAttribute("data-api")) || "";
    if (!apiUrl && currentScript && currentScript.src) {
        try {
            apiUrl = new URL(currentScript.src).origin;
        } catch (e) {
            return;
        }
    }
    if (!apiUrl) return;
    apiUrl = apiUrl.replace(/\/$/, "");

    const siteId = (currentScript && currentScript.getAttribute("data-site-id")) || "";
    const domain = (currentScript && currentScript.getAttribute("data-domain")) || window.location.hostname;
    const mountSelectorAttr = (currentScript && currentScript.getAttribute("data-mount")) || "";

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
                    if (tag === "SCRIPT" || tag === "STYLE" || tag === "IFRAME" || tag === "OBJECT" || tag === "EMBED") {
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
                            if (/^https?:\/\//i.test(href)) el.setAttribute("target", "_blank");
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

    function injectStyles(customCss) {
        if (document.getElementById("omnibanner-embed-style")) return;
        const style = document.createElement("style");
        style.id = "omnibanner-embed-style";
        style.textContent =
            ".ob-banner{box-sizing:border-box;width:100%;padding:10px 44px 10px 18px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;font-weight:500;line-height:1.45;display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:10px;position:relative;box-shadow:0 2px 10px rgba(0,0,0,0.16);z-index:999999;}" +
            ".ob-banner a{color:inherit;text-decoration:underline;font-weight:600;}" +
            ".ob-banner-cta{display:inline-block;padding:4px 12px;border-radius:6px;text-decoration:none !important;font-size:13px;font-weight:600;}" +
            ".ob-banner-countdown{font-family:monospace;font-size:12px;padding:2px 8px;border-radius:999px;background:rgba(0,0,0,0.2);}" +
            ".ob-banner-close{position:absolute;right:12px;top:50%;transform:translateY(-50%);background:transparent;border:none;color:inherit;font-size:20px;line-height:1;cursor:pointer;opacity:0.75;padding:4px 6px;}" +
            ".ob-banner-close:hover{opacity:1;}" +
            ".ob-stack-top-fixed{position:fixed;top:0;left:0;right:0;z-index:999999;display:flex;flex-direction:column;}" +
            ".ob-stack-bottom-fixed{position:fixed;bottom:0;left:0;right:0;z-index:999999;display:flex;flex-direction:column;}" +
            ".ob-stack-floating-br{position:fixed;bottom:16px;right:16px;max-width:420px;width:calc(100vw - 32px);z-index:999999;display:flex;flex-direction:column;gap:8px;}" +
            ".ob-stack-floating-br .ob-banner,.ob-stack-floating-bl .ob-banner{border-radius:10px;text-align:left;justify-content:flex-start;}" +
            ".ob-stack-floating-bl{position:fixed;bottom:16px;left:16px;max-width:420px;width:calc(100vw - 32px);z-index:999999;display:flex;flex-direction:column;gap:8px;}" +
            (customCss || "");
        document.head.appendChild(style);
    }

    function sendEvent(bannerId, eventType) {
        const url = `${apiUrl}/api/public/banner/${encodeURIComponent(bannerId)}/event`;
        const payload = JSON.stringify({ event: eventType, domain: domain, site_id: siteId ? Number(siteId) : null });
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
        return (d > 0 ? d + "d " : "") + String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }

    function matchGlobs(patternsCsv, path) {
        if (!patternsCsv) return false;
        const list = patternsCsv.split(",").map(s => s.trim()).filter(Boolean);
        return list.some(pattern => {
            const regexStr = "^" + pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$";
            return new RegExp(regexStr, "i").test(path);
        });
    }

    function isDismissed(id) {
        const key = `omnibanner_dismissed_${id}`;
        const raw = localStorage.getItem(key);
        if (!raw) return false;
        if (Date.now() < parseInt(raw, 10)) return true;
        localStorage.removeItem(key);
        return false;
    }

    function getOrCreateStack(position, siteConfig) {
        const cls = {
            "top-fixed": "ob-stack-top-fixed",
            "bottom-fixed": "ob-stack-bottom-fixed",
            "floating-br": "ob-stack-floating-br",
            "floating-bl": "ob-stack-floating-bl",
        }[position];
        if (!cls) return null;
        let container = document.querySelector("." + cls);
        if (!container) {
            container = document.createElement("div");
            container.className = cls;
            if (siteConfig && siteConfig.z_index) container.style.zIndex = String(siteConfig.z_index);
            document.body.appendChild(container);
        }
        return container;
    }

    function renderBanner(data, siteConfig) {
        const banner = document.createElement("div");
        banner.className = "ob-banner";
        banner.id = `omnibanner-${data.id}`;
        banner.style.backgroundColor = isSafeColor(data.bg_color, "#ef4444");
        banner.style.color = isSafeColor(data.text_color, "#ffffff");
        if (siteConfig && siteConfig.inherit_font) banner.style.fontFamily = "inherit";
        if (siteConfig && siteConfig.z_index) banner.style.zIndex = String(siteConfig.z_index);

        if (data.icon) {
            const iconSpan = document.createElement("span");
            iconSpan.textContent = data.icon;
            banner.appendChild(iconSpan);
        }

        const msgSpan = document.createElement("span");
        msgSpan.appendChild(sanitizeToFragment(data.text || data.banner_text || ""));
        banner.appendChild(msgSpan);

        if (data.show_countdown && data.scheduled_end) {
            const cd = document.createElement("span");
            cd.className = "ob-banner-countdown";
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
            cta.className = "ob-banner-cta";
            cta.href = data.cta_url;
            cta.textContent = data.cta_text;
            cta.style.backgroundColor = isSafeColor(data.cta_bg_color, "#ffffff");
            cta.style.color = isSafeColor(data.cta_text_color, "#0f172a");
            if (data.cta_new_tab !== false && /^https?:\/\//i.test(data.cta_url)) {
                cta.target = "_blank";
                cta.rel = "noopener noreferrer";
            }
            cta.addEventListener("click", function () { sendEvent(data.id, "click"); });
            banner.appendChild(cta);
        }

        if (data.dismissible) {
            const closeBtn = document.createElement("button");
            closeBtn.className = "ob-banner-close";
            closeBtn.type = "button";
            closeBtn.textContent = "\u00D7";
            closeBtn.setAttribute("aria-label", "Dismiss banner");
            closeBtn.addEventListener("click", function (e) {
                e.preventDefault();
                const hours = Number(data.dismiss_cooldown || 24);
                localStorage.setItem(`omnibanner_dismissed_${data.id}`, String(Date.now() + hours * 3600 * 1000));
                sendEvent(data.id, "dismiss");
                banner.remove();
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
            const mountSel = mountSelectorAttr || (siteConfig && siteConfig.mount_selector) || "";
            const mountTarget = mountSel ? document.querySelector(mountSel) : null;
            if (mountTarget) {
                mountTarget.insertBefore(banner, mountTarget.firstChild);
            } else {
                document.body.insertBefore(banner, document.body.firstChild);
            }
        }

        sendEvent(data.id, "view");

        if (Number(data.display_duration) > 0) {
            setTimeout(function () {
                banner.remove();
            }, Number(data.display_duration) * 1000);
        }
    }

    function boot() {
        const params = new URLSearchParams({
            domain: domain,
            path: window.location.pathname || "/",
            audience: "guest",
        });
        if (siteId) params.set("site_id", siteId);

        fetch(`${apiUrl}/api/public/banners?${params.toString()}`, { mode: "cors" })
            .then(function (resp) { return resp.ok ? resp.json() : null; })
            .then(function (payload) {
                if (!payload || !Array.isArray(payload.banners) || payload.banners.length === 0) return;
                const siteConfig = payload.site_config || {};
                const path = window.location.pathname || "/";
                if (siteConfig.include_paths && !matchGlobs(siteConfig.include_paths, path)) return;
                if (siteConfig.exclude_paths && matchGlobs(siteConfig.exclude_paths, path)) return;

                injectStyles(siteConfig.custom_css || "");
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
                console.warn("OmniBanner embed error:", err);
            });
    }

    if (document.body) {
        boot();
    } else {
        document.addEventListener("DOMContentLoaded", boot);
    }
})();

