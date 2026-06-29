jQuery(document).ready(function($) {
    
    // Sync Branding Click
    $('#omnibanner-sync-btn').on('click', function(e) {
        e.preventDefault();
        
        const apiUrl = $('#omnibanner-api-url').val();
        if (!apiUrl) {
            alert('Please enter a valid OmniBanner API URL before syncing.');
            return;
        }
        
        const $btn = $(this);
        const originalText = $btn.text();
        $btn.text('Syncing...').prop('disabled', true);
        
        $.ajax({
            url: omnibanner_admin.ajax_url,
            method: 'POST',
            data: {
                action: 'omnibanner_refresh_branding',
                nonce: omnibanner_admin.nonce,
                api_url: apiUrl
            },
            success: function(response) {
                if (response.success) {
                    // Update form values
                    $('#omnibanner-cached-name').val(response.data.name);
                    $('#omnibanner-cached-icon').val(response.data.icon_url);
                    $('#omnibanner-cached-color').val(response.data.color);
                    
                    // Update page style accents dynamically
                    $('.omnibanner-admin-wrap').css('--brand-color', response.data.color);
                    
                    // Update header logo and title
                    $('.omnibanner-title').text(response.data.name + ' Settings');
                    $('.omnibanner-brand-icon').attr('src', response.data.icon_url);
                    
                    alert('Branding successfully synced! Save options to finalize.');
                } else {
                    alert('Sync failed: ' + (response.data.message || 'Unknown server error'));
                }
            },
            error: function() {
                alert('AJAX request failed. Check network parameters.');
            },
            complete: function() {
                $btn.text(originalText).prop('disabled', false);
            }
        });
    });

    // Run Diagnostics Suite
    $('#omnibanner-run-diag-btn').on('click', function(e) {
        e.preventDefault();
        const apiUrl = $('#omnibanner-api-url').val();
        if (!apiUrl) {
            alert('Please enter the API URL first.');
            return;
        }

        const $btn = $(this);
        $btn.text('Running Diagnostics...').prop('disabled', true);

        // Reset badges to loading
        $('.diag-badge').removeClass('green red').addClass('gray').text('Checking...');

        $.ajax({
            url: omnibanner_admin.ajax_url,
            method: 'POST',
            data: {
                action: 'omnibanner_run_diagnostics',
                nonce: omnibanner_admin.nonce,
                api_url: apiUrl
            },
            success: function(response) {
                if (response.success) {
                    const data = response.data;
                    
                    // Conn
                    updateDiagRow('conn', data.conn);
                    // Info
                    updateDiagRow('info', data.info);
                    // Banner
                    updateDiagRow('banner', data.banner);
                } else {
                    alert('Diagnostics run failed: ' + response.data.message);
                }
            },
            error: function() {
                alert('Failed to connect to WordPress AJAX server.');
            },
            complete: function() {
                $btn.text('🔬 Run Diagnostics Suite').prop('disabled', false);
            }
        });
    });

    function updateDiagRow(key, check) {
        const $status = $('#diag-status-' + key);
        const $time = $('#diag-time-' + key);
        
        $status.empty();
        if (check.status === 'success') {
            $status.append($('<span class="diag-badge green">').text(check.message));
            $time.text(check.time + ' ms');
        } else {
            $status.append($('<span class="diag-badge red">').text(check.message || 'Failed'));
            $time.text('-');
        }
    }

    // Flush Cached Options
    $('#omnibanner-flush-btn').on('click', function(e) {
        e.preventDefault();
        if (!confirm('Are you sure you want to flush the cached branding logo, titles, and styles? This will revert back to defaults.')) {
            return;
        }

        const $btn = $(this);
        $btn.prop('disabled', true);

        $.ajax({
            url: omnibanner_admin.ajax_url,
            method: 'POST',
            data: {
                action: 'omnibanner_flush_cache',
                nonce: omnibanner_admin.nonce
            },
            success: function(response) {
                if (response.success) {
                    alert('Branding cache successfully flushed. Reloading settings page.');
                    window.location.reload();
                } else {
                    alert('Failed to flush cache.');
                }
            },
            error: function() {
                alert('AJAX request failed.');
            },
            complete: function() {
                $btn.prop('disabled', false);
            }
        });
    });
});

