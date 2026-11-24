jQuery(document).ready(function ($) {
    function showAdminNotice(message, type) {
        const $box = $('#omnibanner-admin-notice');
        $box.removeClass('notice-success notice-error notice-info')
            .addClass(type === 'error' ? 'notice-error' : (type === 'info' ? 'notice-info' : 'notice-success'))
            .show();
        $box.find('p').text(message);
    }

    function claimOnboardingLink(linkVal, $btn, defaultText) {
        if (!linkVal) {
            showAdminNotice(omnibanner_admin.i18n.enter_onboarding, 'error');
            return;
        }
        $btn.text(omnibanner_admin.i18n.connecting).prop('disabled', true);

        $.ajax({
            url: omnibanner_admin.ajax_url,
            method: 'POST',
            data: {
                action: 'omnibanner_claim_onboarding',
                nonce: omnibanner_admin.nonce,
                onboarding_link: linkVal
            },
            success: function (response) {
                if (response.success) {
                    showAdminNotice(response.data.message, 'success');
                    setTimeout(function () {
                        const url = new URL(window.location.href);
                        url.searchParams.delete('ob_pair');
                        window.location.href = url.toString();
                    }, 900);
                } else {
                    showAdminNotice((response.data && response.data.message) || 'Connection failed.', 'error');
                }
            },
            error: function () {
                showAdminNotice('AJAX request failed while connecting.', 'error');
            },
            complete: function () {
                $btn.text(defaultText).prop('disabled', false);
            }
        });
    }

    $('#omnibanner-pair-btn').on('click', function (e) {
        e.preventDefault();
        claimOnboardingLink($('#omnibanner-onboarding-link').val().trim(), $(this), omnibanner_admin.i18n.connect_btn);
    });

    $('#omnibanner-confirm-deeplink-btn').on('click', function (e) {
        e.preventDefault();
        const $btn = $(this);
        claimOnboardingLink($('#omnibanner-deeplink-url').val().trim(), $btn, $btn.text());
    });

    $('#omnibanner-sync-btn').on('click', function (e) {
        e.preventDefault();
        const apiUrl = $('#omnibanner-api-url').val().trim();
        if (!apiUrl) {
            showAdminNotice(omnibanner_admin.i18n.enter_api_url, 'error');
            return;
        }

        const $btn = $(this);
        $btn.text(omnibanner_admin.i18n.syncing).prop('disabled', true);

        $.ajax({
            url: omnibanner_admin.ajax_url,
            method: 'POST',
            data: {
                action: 'omnibanner_refresh_branding',
                nonce: omnibanner_admin.nonce,
                api_url: apiUrl
            },
            success: function (response) {
                if (response.success) {
                    $('#omnibanner-cached-name').val(response.data.name);
                    $('#omnibanner-cached-icon').val(response.data.icon_url);
                    $('#omnibanner-cached-color').val(response.data.color);
                    $('.omnibanner-admin-wrap').css('--brand-color', response.data.color);
                    $('.omnibanner-title').text(response.data.name + ' Settings');
                    if (response.data.icon_url) {
                        $('.omnibanner-brand-icon').attr('src', response.data.icon_url);
                    }
                    showAdminNotice('Branding and site configuration synced!', 'success');
                } else {
                    showAdminNotice((response.data && response.data.message) || 'Sync failed.', 'error');
                }
            },
            error: function () {
                showAdminNotice('AJAX sync request failed.', 'error');
            },
            complete: function () {
                $btn.text(omnibanner_admin.i18n.sync_btn).prop('disabled', false);
            }
        });
    });

    $('#omnibanner-disconnect-btn').on('click', function (e) {
        e.preventDefault();
        const $btn = $(this);
        $btn.prop('disabled', true);
        $.ajax({
            url: omnibanner_admin.ajax_url,
            method: 'POST',
            data: {
                action: 'omnibanner_disconnect_site',
                nonce: omnibanner_admin.nonce
            },
            success: function (response) {
                if (response.success) {
                    showAdminNotice(response.data.message, 'info');
                    setTimeout(function () { window.location.reload(); }, 600);
                }
            },
            complete: function () {
                $btn.prop('disabled', false);
            }
        });
    });

    function updateDiagRow(key, check) {
        const $status = $('#diag-status-' + key);
        const $time = $('#diag-time-' + key);
        $status.empty();
        if (check && check.status === 'success') {
            $status.append($('<span class="diag-badge green">').text(check.message));
            $time.text(check.time + ' ms');
        } else {
            $status.append($('<span class="diag-badge red">').text((check && check.message) || 'Failed'));
            $time.text('-');
        }
    }

    $('#omnibanner-run-diag-btn').on('click', function (e) {
        e.preventDefault();
        const apiUrl = $('#omnibanner-api-url').val().trim();
        if (!apiUrl) {
            showAdminNotice(omnibanner_admin.i18n.enter_api_url, 'error');
            return;
        }
        const $btn = $(this);
        $btn.text(omnibanner_admin.i18n.diag_running).prop('disabled', true);
        $('.diag-badge').not('.omnibanner-header .diag-badge').removeClass('green red').addClass('gray').text('Checking...');

        $.ajax({
            url: omnibanner_admin.ajax_url,
            method: 'POST',
            data: {
                action: 'omnibanner_run_diagnostics',
                nonce: omnibanner_admin.nonce,
                api_url: apiUrl
            },
            success: function (response) {
                if (response.success) {
                    updateDiagRow('conn', response.data.conn);
                    updateDiagRow('info', response.data.info);
                    updateDiagRow('banner', response.data.banner);
                } else {
                    showAdminNotice((response.data && response.data.message) || 'Diagnostics failed.', 'error');
                }
            },
            error: function () {
                showAdminNotice('Failed to run diagnostics.', 'error');
            },
            complete: function () {
                $btn.text(omnibanner_admin.i18n.diag_btn).prop('disabled', false);
            }
        });
    });

    $('#omnibanner-flush-btn').on('click', function (e) {
        e.preventDefault();
        const $btn = $(this);
        $btn.prop('disabled', true);
        $.ajax({
            url: omnibanner_admin.ajax_url,
            method: 'POST',
            data: {
                action: 'omnibanner_flush_cache',
                nonce: omnibanner_admin.nonce
            },
            success: function (response) {
                if (response.success) {
                    showAdminNotice(response.data.message, 'success');
                    setTimeout(function () { window.location.reload(); }, 600);
                }
            },
            complete: function () {
                $btn.prop('disabled', false);
            }
        });
    });

    $('#omnibanner-check-updates-btn').on('click', function (e) {
        e.preventDefault();
        const $btn = $(this);
        const $status = $('#omnibanner-update-status');
        $btn.text(omnibanner_admin.i18n.checking_updates).prop('disabled', true);
        $status.empty().append($('<span class="diag-badge gray">').text('Checking...'));

        $.ajax({
            url: omnibanner_admin.ajax_url,
            method: 'POST',
            data: {
                action: 'omnibanner_check_updates',
                nonce: omnibanner_admin.nonce
            },
            success: function (response) {
                $status.empty();
                if (response.success) {
                    const d = response.data;
                    if (d.update_available) {
                        $status.append($('<span class="diag-badge red">').text('Update available: v' + d.remote_version));
                        showAdminNotice('OmniBanner v' + d.remote_version + ' is available on your server. Install or enable auto-updates on the Plugins page.', 'info');
                    } else {
                        $status.append($('<span class="diag-badge green">').text('Up to date (v' + d.remote_version + ')'));
                        showAdminNotice('OmniBanner is up to date (v' + d.installed_version + ', SHA-256 ' + d.sha256_prefix + '...).', 'success');
                    }
                } else {
                    const msg = (response.data && response.data.message) || 'Update check failed.';
                    $status.append($('<span class="diag-badge red">').text('Check failed'));
                    showAdminNotice(msg, 'error');
                }
            },
            error: function () {
                $status.empty().append($('<span class="diag-badge red">').text('AJAX error'));
                showAdminNotice('Failed to contact WordPress update checker.', 'error');
            },
            complete: function () {
                $btn.text(omnibanner_admin.i18n.check_updates_btn).prop('disabled', false);
            }
        });
    });
});
