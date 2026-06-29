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
});
