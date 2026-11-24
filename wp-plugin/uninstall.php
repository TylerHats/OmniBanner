<?php
if (!defined('WP_UNINSTALL_PLUGIN')) {
    exit;
}

$omnibanner_options = array(
    'omnibanner_api_url',
    'omnibanner_site_id',
    'omnibanner_site_key',
    'omnibanner_cached_name',
    'omnibanner_cached_icon',
    'omnibanner_cached_color',
    'omnibanner_site_config',
    'omnibanner_local_enabled',
    'omnibanner_reported_version',
);

foreach ($omnibanner_options as $option_name) {
    delete_option($option_name);
    delete_site_option($option_name);
}

delete_transient('omnibanner_update_manifest');

