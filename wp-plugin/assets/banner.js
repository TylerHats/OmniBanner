(function() {
    // Only execute if config variables are localized
    if (typeof OmniBannerConf === 'undefined') {
        return;
    }

    const { api_url, domain } = OmniBannerConf;

    // Async Fetch active banner
    const fetchUrl = `${api_url.replace(/\/$/, '')}/api/public/banner?domain=${encodeURIComponent(domain)}`;

    fetch(fetchUrl)
        .then(response => {
            if (!response.ok) {
                throw new Error('Banner response failed');
            }
            return response.json();
        })
        .then(data => {
            // Check if there is an active notice banner returning
            if (!data || !data.id || !data.text) {
                return;
            }

            const bannerId = data.id;
            const cooldownKey = `omnibanner_dismissed_${bannerId}`;
            
            // Check dismiss cooldown in localStorage
            const dismissTime = localStorage.getItem(cooldownKey);
            if (dismissTime) {
                const now = new Date().getTime();
                if (now < parseInt(dismissTime)) {
                    return; // Notice remains dismissed
                } else {
                    localStorage.removeItem(cooldownKey); // Cooldown expired
                }
            }

            // Render Banner
            createBanner(data, cooldownKey);
        })
        .catch(err => {
            console.warn('OmniBanner notice fetch error:', err);
        });

    function createBanner(data, cooldownKey) {
        const banner = document.createElement('div');
        banner.className = 'omnibanner-notice';
        banner.id = `omnibanner-${data.id}`;
        banner.style.backgroundColor = data.bg_color || '#ef4444';
        banner.style.color = data.text_color || '#ffffff';
        
        // Emojis and links are rendered raw as HTML
        banner.innerHTML = data.text;

        // Create close button if dismissible
        if (data.dismissible) {
            const closeBtn = document.createElement('button');
            closeBtn.className = 'omnibanner-close-btn';
            closeBtn.innerHTML = '&times;';
            closeBtn.setAttribute('aria-label', 'Dismiss banner');
            closeBtn.addEventListener('click', function(e) {
                e.preventDefault();
                dismissBanner(banner, data.dismiss_cooldown, cooldownKey);
            });
            banner.appendChild(closeBtn);
        }

        // Inject at the very top of body
        if (document.body) {
            document.body.insertBefore(banner, document.body.firstChild);
        } else {
            // Document loading fallback
            document.addEventListener('DOMContentLoaded', () => {
                document.body.insertBefore(banner, document.body.firstChild);
            });
        }

        // Handle auto-dismiss duration timer
        if (data.display_duration > 0) {
            setTimeout(() => {
                dismissBanner(banner, data.dismiss_cooldown, cooldownKey, false); // Auto dismiss doesn't block re-displays necessarily if user didn't hit Close, but it is best to set dismiss timestamp too
            }, data.display_duration * 1000);
        }
    }

    function dismissBanner(bannerEl, cooldownHours, cooldownKey, applyCooldown = true) {
        if (applyCooldown) {
            const now = new Date().getTime();
            const expires = now + (cooldownHours * 60 * 60 * 1000);
            localStorage.setItem(cooldownKey, expires);
        }

        // Add slide-up transition classes
        bannerEl.classList.add('omnibanner-fade-out');
        
        // Remove from DOM after animation completes
        setTimeout(() => {
            bannerEl.remove();
        }, 310);
    }
})();
