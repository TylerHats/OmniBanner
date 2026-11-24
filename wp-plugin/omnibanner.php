<?php
/**
 * Plugin Name: OmniBanner
 * Plugin URI: https://github.com/TylerHats/OmniBanner
 * Description: Connect your WordPress site to a self-hosted OmniBanner server using a one-time onboarding link to display cache-safe scheduled notices.
 * Version: 2.0.0
 * Requires at least: 5.8
 * Requires PHP: 7.4
 * Author: Tyler Hatfield
 * License: GPLv3 or later
 * License URI: https://www.gnu.org/licenses/gpl-3.0.html
 * Text Domain: omnibanner
 */

if (!defined('ABSPATH')) {
    exit;
}

define('OMNIBANNER_VERSION', '2.0.0');

class OmniBannerPlugin {

    public function __construct() {
        add_action('admin_menu', array($this, 'add_admin_menu'));
        add_action('admin_init', array($this, 'register_settings'));
        add_action('admin_enqueue_scripts', array($this, 'enqueue_admin_assets'));
        add_filter('plugin_action_links_' . plugin_basename(__FILE__), array($this, 'add_plugin_action_links'));

        add_action('wp_enqueue_scripts', array($this, 'enqueue_frontend_assets'));

        add_action('wp_ajax_omnibanner_claim_onboarding', array($this, 'ajax_claim_onboarding'));
        add_action('wp_ajax_omnibanner_refresh_branding', array($this, 'ajax_refresh_branding'));
        add_action('wp_ajax_omnibanner_disconnect_site', array($this, 'ajax_disconnect_site'));
        add_action('wp_ajax_omnibanner_run_diagnostics', array($this, 'ajax_run_diagnostics'));
        add_action('wp_ajax_omnibanner_flush_cache', array($this, 'ajax_flush_cache'));
    }

    public function add_plugin_action_links($links) {
        $url = admin_url('options-general.php?page=omnibanner-settings');
        $settings_link = '<a href="' . esc_url($url) . '">' . esc_html__('Settings', 'omnibanner') . '</a>';
        array_unshift($links, $settings_link);
        return $links;
    }

    public function add_admin_menu() {
        $app_name = get_option('omnibanner_cached_name', 'OmniBanner');
        if (empty($app_name)) {
            $app_name = 'OmniBanner';
        }
        add_options_page(
            $app_name . ' ' . __('Settings', 'omnibanner'),
            $app_name,
            'manage_options',
            'omnibanner-settings',
            array($this, 'render_settings_page')
        );
    }

    public function register_settings() {
        register_setting('omnibanner_options', 'omnibanner_api_url', array(
            'type' => 'string',
            'sanitize_callback' => array($this, 'sanitize_api_url'),
            'default' => '',
        ));
        register_setting('omnibanner_options', 'omnibanner_site_id', array(
            'type' => 'integer',
            'sanitize_callback' => 'absint',
            'default' => 0,
        ));
        register_setting('omnibanner_options', 'omnibanner_site_key', array(
            'type' => 'string',
            'sanitize_callback' => 'sanitize_text_field',
            'default' => '',
        ));
        register_setting('omnibanner_options', 'omnibanner_cached_name', array(
            'type' => 'string',
            'sanitize_callback' => 'sanitize_text_field',
            'default' => 'OmniBanner',
        ));
        register_setting('omnibanner_options', 'omnibanner_cached_icon', array(
            'type' => 'string',
            'sanitize_callback' => 'esc_url_raw',
            'default' => '',
        ));
        register_setting('omnibanner_options', 'omnibanner_cached_color', array(
            'type' => 'string',
            'sanitize_callback' => array($this, 'sanitize_color'),
            'default' => '#8b5cf6',
        ));
        register_setting('omnibanner_options', 'omnibanner_site_config', array(
            'type' => 'string',
            'sanitize_callback' => array($this, 'sanitize_json_string'),
            'default' => '{}',
        ));
        register_setting('omnibanner_options', 'omnibanner_local_enabled', array(
            'type' => 'boolean',
            'sanitize_callback' => 'rest_sanitize_boolean',
            'default' => true,
        ));
    }

    public function sanitize_api_url($value) {
        return untrailingslashit(esc_url_raw($value));
    }

    public function sanitize_color($value) {
        $color = sanitize_hex_color($value);
        return $color ? $color : '#8b5cf6';
    }

    public function sanitize_json_string($value) {
        if (is_array($value)) {
            return wp_json_encode($value);
        }
        $decoded = json_decode((string) $value, true);
        return is_array($decoded) ? wp_json_encode($decoded) : '{}';
    }

    private function http_args($timeout = 10, $extra_headers = array()) {
        return array(
            'timeout' => $timeout,
            'sslverify' => apply_filters('omnibanner_http_sslverify', true),
            'headers' => $extra_headers,
        );
    }

    public function enqueue_admin_assets($hook) {
        if ($hook !== 'settings_page_omnibanner-settings') {
            return;
        }
        wp_enqueue_style('omnibanner-admin-css', plugin_dir_url(__FILE__) . 'assets/admin.css', array(), OMNIBANNER_VERSION);
        wp_enqueue_script('omnibanner-admin-js', plugin_dir_url(__FILE__) . 'assets/admin.js', array('jquery'), OMNIBANNER_VERSION, true);
        wp_localize_script('omnibanner-admin-js', 'omnibanner_admin', array(
            'ajax_url' => admin_url('admin-ajax.php'),
            'nonce' => wp_create_nonce('omnibanner_admin_nonce'),
            'i18n' => array(
                'connecting' => __('Connecting...', 'omnibanner'),
                'connect_btn' => __('Connect Site', 'omnibanner'),
                'syncing' => __('Syncing...', 'omnibanner'),
                'sync_btn' => __('Sync Branding & Config', 'omnibanner'),
                'diag_running' => __('Running Diagnostics...', 'omnibanner'),
                'diag_btn' => __('Run Diagnostics Suite', 'omnibanner'),
                'enter_onboarding' => __('Please paste a valid OmniBanner onboarding link.', 'omnibanner'),
                'enter_api_url' => __('Please enter a valid OmniBanner URL first.', 'omnibanner'),
            ),
        ));
    }

    public function enqueue_frontend_assets() {
        $api_url = get_option('omnibanner_api_url', '');
        if (empty($api_url)) {
            return;
        }

        $site_config_raw = get_option('omnibanner_site_config', '{}');
        $site_config = json_decode($site_config_raw, true);
        if (!is_array($site_config)) {
            $site_config = array();
        }
        $lock_local = !empty($site_config['lock_local_overrides']);
        $local_enabled = (bool) get_option('omnibanner_local_enabled', true);
        if (!$lock_local && !$local_enabled) {
            return;
        }

        wp_enqueue_style('omnibanner-frontend-css', plugin_dir_url(__FILE__) . 'assets/banner.css', array(), OMNIBANNER_VERSION);
        $script_args = version_compare($GLOBALS['wp_version'], '6.3', '>=')
            ? array('in_footer' => true, 'strategy' => 'defer')
            : true;
        wp_enqueue_script('omnibanner-frontend-js', plugin_dir_url(__FILE__) . 'assets/banner.js', array(), OMNIBANNER_VERSION, $script_args);

        $domain = wp_parse_url(home_url(), PHP_URL_HOST);
        $site_id = absint(get_option('omnibanner_site_id', 0));

        $is_logged_in = is_user_logged_in();
        $user_roles = array();
        if ($is_logged_in) {
            $user = wp_get_current_user();
            if ($user && !empty($user->roles)) {
                $user_roles = array_values($user->roles);
            }
        }

        wp_localize_script('omnibanner-frontend-js', 'OmniBannerConf', array(
            'api_url' => esc_url_raw(untrailingslashit($api_url)),
            'domain' => sanitize_text_field($domain ? $domain : ''),
            'site_id' => $site_id,
            'site_config' => $site_config,
            'is_logged_in' => $is_logged_in,
            'user_roles' => $user_roles,
        ));
    }

    public function render_settings_page() {
        if (!current_user_can('manage_options')) {
            return;
        }

        $api_url = get_option('omnibanner_api_url', '');
        $site_id = absint(get_option('omnibanner_site_id', 0));
        $site_key = get_option('omnibanner_site_key', '');
        $app_name = get_option('omnibanner_cached_name', 'OmniBanner');
        $app_icon = get_option('omnibanner_cached_icon', '');
        $app_color = get_option('omnibanner_cached_color', '#8b5cf6');
        $local_enabled = (bool) get_option('omnibanner_local_enabled', true);

        if (empty($app_icon)) {
            $app_icon = plugin_dir_url(__FILE__) . 'assets/logo.png';
        }

        $deep_link_pair = '';
        if (isset($_GET['ob_pair'])) { // phpcs:ignore WordPress.Security.NonceVerification.Recommended
            $deep_link_pair = esc_url_raw(wp_unslash($_GET['ob_pair'])); // phpcs:ignore WordPress.Security.NonceVerification.Recommended
        }
        $deep_link_host = $deep_link_pair ? wp_parse_url($deep_link_pair, PHP_URL_HOST) : '';
        $is_paired = !empty($api_url) && !empty($site_key) && $site_id > 0;
        $site_config_decoded = json_decode(get_option('omnibanner_site_config', '{}'), true);
        $lock_local = is_array($site_config_decoded) && !empty($site_config_decoded['lock_local_overrides']);
        ?>
        <div class="wrap omnibanner-admin-wrap" style="--brand-color: <?php echo esc_attr($app_color); ?>;">
            <div class="omnibanner-header">
                <img src="<?php echo esc_url($app_icon); ?>" alt="<?php echo esc_attr($app_name); ?>" class="omnibanner-brand-icon">
                <div>
                    <h1 class="omnibanner-title"><?php echo esc_html($app_name); ?> <?php esc_html_e('Settings', 'omnibanner'); ?></h1>
                    <?php if ($is_paired) : ?>
                        <span class="diag-badge green"><?php echo esc_html(sprintf(__('Connected (Site #%d)', 'omnibanner'), $site_id)); ?></span>
                    <?php elseif (!empty($api_url)) : ?>
                        <span class="diag-badge gray"><?php esc_html_e('Manual URL Configured', 'omnibanner'); ?></span>
                    <?php else : ?>
                        <span class="diag-badge gray"><?php esc_html_e('Not Connected', 'omnibanner'); ?></span>
                    <?php endif; ?>
                </div>
            </div>

            <div id="omnibanner-admin-notice" class="notice" style="display: none;"><p></p></div>

            <?php if (!empty($deep_link_pair)) : ?>
                <div class="card omnibanner-card omnibanner-deeplink-card">
                    <h2 class="title"><?php esc_html_e('Confirm Onboarding Connection', 'omnibanner'); ?></h2>
                    <p>
                        <?php
                        echo esc_html(
                            sprintf(
                                /* translators: %s: remote OmniBanner hostname */
                                __('You opened an onboarding link for the OmniBanner server at %s. Click below to confirm pairing this WordPress site.', 'omnibanner'),
                                $deep_link_host ? $deep_link_host : $deep_link_pair
                            )
                        );
                        ?>
                    </p>
                    <input type="hidden" id="omnibanner-deeplink-url" value="<?php echo esc_attr($deep_link_pair); ?>">
                    <button type="button" class="button button-primary" id="omnibanner-confirm-deeplink-btn">
                        <?php esc_html_e('Confirm & Connect Site', 'omnibanner'); ?>
                    </button>
                </div>
            <?php endif; ?>

            <div class="card omnibanner-card">
                <h2 class="title"><?php esc_html_e('Quick Connect via Onboarding Link', 'omnibanner'); ?></h2>
                <p><?php esc_html_e('Generate a one-time onboarding link in your OmniBanner dashboard (Sites tab) and paste it below to configure branding, site identity, and per-site controls automatically.', 'omnibanner'); ?></p>
                <div class="omnibanner-pair-row">
                    <input type="url" id="omnibanner-onboarding-link" class="regular-text" placeholder="https://banners.example.com/pair/obp_..." value="<?php echo esc_attr($deep_link_pair); ?>">
                    <button type="button" class="button button-primary" id="omnibanner-pair-btn"><?php esc_html_e('Connect Site', 'omnibanner'); ?></button>
                </div>
            </div>

            <div class="card omnibanner-card" style="margin-top: 20px;">
                <h2 class="title"><?php esc_html_e('Connection & Display Settings', 'omnibanner'); ?></h2>

                <form method="post" action="options.php" id="omnibanner-config-form">
                    <?php settings_fields('omnibanner_options'); ?>
                    <?php do_settings_sections('omnibanner_options'); ?>

                    <table class="form-table">
                        <tr valign="top">
                            <th scope="row"><label for="omnibanner-api-url"><?php esc_html_e('OmniBanner Service URL', 'omnibanner'); ?></label></th>
                            <td>
                                <input type="url" name="omnibanner_api_url" id="omnibanner-api-url" value="<?php echo esc_attr($api_url); ?>" class="regular-text" placeholder="https://banners.example.com">
                                <button type="button" class="button button-secondary" id="omnibanner-sync-btn"><?php esc_html_e('Sync Branding & Config', 'omnibanner'); ?></button>
                                <p class="description"><?php esc_html_e('Base URL of your central OmniBanner service.', 'omnibanner'); ?></p>
                            </td>
                        </tr>
                        <tr valign="top">
                            <th scope="row"><label for="omnibanner-local-enabled"><?php esc_html_e('Enable Frontend Banners', 'omnibanner'); ?></label></th>
                            <td>
                                <?php if ($lock_local) : ?>
                                    <input type="hidden" name="omnibanner_local_enabled" value="<?php echo $local_enabled ? '1' : '0'; ?>">
                                    <label>
                                        <input type="checkbox" id="omnibanner-local-enabled" checked disabled>
                                        <?php esc_html_e('Controlled centrally by your OmniBanner server (local override locked)', 'omnibanner'); ?>
                                    </label>
                                <?php else : ?>
                                    <label>
                                        <input type="checkbox" name="omnibanner_local_enabled" id="omnibanner-local-enabled" value="1" <?php checked($local_enabled, true); ?>>
                                        <?php esc_html_e('Render active OmniBanner notices on frontend pages', 'omnibanner'); ?>
                                    </label>
                                <?php endif; ?>
                            </td>
                        </tr>
                    </table>

                    <input type="hidden" name="omnibanner_site_id" id="omnibanner-site-id" value="<?php echo esc_attr($site_id); ?>">
                    <input type="hidden" name="omnibanner_site_key" id="omnibanner-site-key" value="<?php echo esc_attr($site_key); ?>">
                    <input type="hidden" name="omnibanner_cached_name" id="omnibanner-cached-name" value="<?php echo esc_attr($app_name); ?>">
                    <input type="hidden" name="omnibanner_cached_icon" id="omnibanner-cached-icon" value="<?php echo esc_url($app_icon); ?>">
                    <input type="hidden" name="omnibanner_cached_color" id="omnibanner-cached-color" value="<?php echo esc_attr($app_color); ?>">
                    <input type="hidden" name="omnibanner_site_config" id="omnibanner-site-config" value="<?php echo esc_attr(get_option('omnibanner_site_config', '{}')); ?>">

                    <div style="display: flex; gap: 10px; align-items: center;">
                        <?php submit_button(__('Save Settings', 'omnibanner'), 'primary', 'submit', false); ?>
                        <?php if ($is_paired) : ?>
                            <button type="button" class="button button-secondary" id="omnibanner-disconnect-btn"><?php esc_html_e('Disconnect Site', 'omnibanner'); ?></button>
                        <?php endif; ?>
                    </div>
                </form>
            </div>

            <div class="card omnibanner-card" style="margin-top: 20px;">
                <h2 class="title"><?php esc_html_e('Integration Diagnostics', 'omnibanner'); ?></h2>
                <p><?php esc_html_e('Verify connectivity, branding sync, and banner API reachability without inflating visitor view metrics.', 'omnibanner'); ?></p>

                <table class="widefat fixed striped" style="margin-top: 15px; margin-bottom: 15px;">
                    <thead>
                        <tr>
                            <th style="width: 250px; padding: 10px;"><?php esc_html_e('Diagnostic Check', 'omnibanner'); ?></th>
                            <th style="padding: 10px;"><?php esc_html_e('Status', 'omnibanner'); ?></th>
                            <th style="width: 100px; text-align: right; padding: 10px;"><?php esc_html_e('Latency', 'omnibanner'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td style="padding: 10px;"><strong><?php esc_html_e('Service Health (/api/health)', 'omnibanner'); ?></strong></td>
                            <td id="diag-status-conn" style="padding: 10px;"><span class="diag-badge gray"><?php esc_html_e('Not Tested', 'omnibanner'); ?></span></td>
                            <td id="diag-time-conn" style="text-align: right; color: #888; padding: 10px;">-</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px;"><strong><?php esc_html_e('Branding Info (/api/public/info)', 'omnibanner'); ?></strong></td>
                            <td id="diag-status-info" style="padding: 10px;"><span class="diag-badge gray"><?php esc_html_e('Not Tested', 'omnibanner'); ?></span></td>
                            <td id="diag-time-info" style="text-align: right; color: #888; padding: 10px;">-</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px;"><strong><?php esc_html_e('Banners Feed (/api/public/banners)', 'omnibanner'); ?></strong></td>
                            <td id="diag-status-banner" style="padding: 10px;"><span class="diag-badge gray"><?php esc_html_e('Not Tested', 'omnibanner'); ?></span></td>
                            <td id="diag-time-banner" style="text-align: right; color: #888; padding: 10px;">-</td>
                        </tr>
                    </tbody>
                </table>

                <div style="display: flex; gap: 10px;">
                    <button type="button" class="button button-primary" id="omnibanner-run-diag-btn"><?php esc_html_e('Run Diagnostics Suite', 'omnibanner'); ?></button>
                    <button type="button" class="button button-secondary" id="omnibanner-flush-btn"><?php esc_html_e('Reset Cached Branding', 'omnibanner'); ?></button>
                </div>
            </div>
        </div>
        <?php
    }

    private function parse_onboarding_link($raw_input) {
        $raw = trim((string) $raw_input);
        if (empty($raw)) {
            return null;
        }
        $parsed = wp_parse_url($raw);
        if (!$parsed || empty($parsed['scheme']) || empty($parsed['host'])) {
            return null;
        }
        $scheme = strtolower($parsed['scheme']);
        if (!in_array($scheme, array('http', 'https'), true)) {
            return null;
        }
        $base_url = $scheme . '://' . $parsed['host'];
        if (!empty($parsed['port'])) {
            $base_url .= ':' . intval($parsed['port']);
        }
        $path = isset($parsed['path']) ? trim($parsed['path'], '/') : '';
        $token = '';
        $pos = strpos($path, 'pair/');
        if ($pos !== false) {
            $base_prefix = trim(substr($path, 0, $pos), '/');
            if ($base_prefix !== '') {
                $base_url .= '/' . $base_prefix;
            }
            $token = substr($path, $pos + 5);
        }
        if (empty($token)) {
            return null;
        }
        return array(
            'base_url' => untrailingslashit($base_url),
            'token' => sanitize_text_field($token),
        );
    }

    private function apply_remote_branding($api_url, $branding) {
        if (!is_array($branding)) {
            return array();
        }
        $name = isset($branding['name']) ? sanitize_text_field($branding['name']) : 'OmniBanner';
        $icon = isset($branding['icon_url']) ? $branding['icon_url'] : '';
        if ($icon && strpos($icon, 'http') !== 0) {
            $icon = untrailingslashit($api_url) . '/' . ltrim($icon, '/');
        }
        $icon = esc_url_raw($icon);
        $color = isset($branding['color']) ? $this->sanitize_color($branding['color']) : '#8b5cf6';

        update_option('omnibanner_cached_name', $name);
        update_option('omnibanner_cached_icon', $icon);
        update_option('omnibanner_cached_color', $color);

        return array(
            'name' => $name,
            'icon_url' => $icon,
            'color' => $color,
        );
    }

    public function ajax_claim_onboarding() {
        check_ajax_referer('omnibanner_admin_nonce', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_send_json_error(array('message' => __('Unauthorized access.', 'omnibanner')));
        }

        $raw_link = isset($_POST['onboarding_link']) ? esc_url_raw(wp_unslash($_POST['onboarding_link'])) : '';
        $parsed = $this->parse_onboarding_link($raw_link);
        if (!$parsed) {
            wp_send_json_error(array('message' => __('Invalid onboarding link. Expected format: https://server/pair/obp_...', 'omnibanner')));
        }

        $api_url = $parsed['base_url'];
        $domain = wp_parse_url(home_url(), PHP_URL_HOST);

        $args = $this->http_args(12, array('Content-Type' => 'application/json'));
        $args['body'] = wp_json_encode(array(
            'token' => $parsed['token'],
            'domain' => $domain ? $domain : '',
            'site_url' => home_url(),
            'site_name' => get_bloginfo('name'),
            'wp_version' => get_bloginfo('version'),
            'plugin_version' => OMNIBANNER_VERSION,
        ));

        $response = wp_remote_post($api_url . '/api/sites/claim', $args);
        if (is_wp_error($response)) {
            wp_send_json_error(array('message' => $response->get_error_message()));
        }

        $code = wp_remote_retrieve_response_code($response);
        $body = json_decode(wp_remote_retrieve_body($response), true);
        if ($code !== 200 || !is_array($body) || empty($body['site_key'])) {
            $detail = is_array($body) && isset($body['detail']) ? sanitize_text_field($body['detail']) : ('HTTP ' . $code);
            wp_send_json_error(array('message' => $detail));
        }

        update_option('omnibanner_api_url', $this->sanitize_api_url($api_url));
        update_option('omnibanner_site_id', absint($body['site_id']));
        update_option('omnibanner_site_key', sanitize_text_field($body['site_key']));
        update_option('omnibanner_local_enabled', true);

        if (isset($body['settings']) && is_array($body['settings'])) {
            update_option('omnibanner_site_config', wp_json_encode($body['settings']));
        }
        $branding = $this->apply_remote_branding($api_url, isset($body['branding']) ? $body['branding'] : array());

        wp_send_json_success(array(
            'api_url' => $api_url,
            'site_id' => absint($body['site_id']),
            'branding' => $branding,
            'message' => __('Site successfully paired and synced with OmniBanner!', 'omnibanner'),
        ));
    }

    public function ajax_refresh_branding() {
        check_ajax_referer('omnibanner_admin_nonce', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_send_json_error(array('message' => __('Unauthorized access.', 'omnibanner')));
        }

        $api_url = isset($_POST['api_url']) ? $this->sanitize_api_url(wp_unslash($_POST['api_url'])) : '';
        if (empty($api_url)) {
            wp_send_json_error(array('message' => __('Empty API URL endpoint.', 'omnibanner')));
        }

        $site_key = get_option('omnibanner_site_key', '');
        if (!empty($site_key)) {
            $args = $this->http_args(10, array(
                'Content-Type' => 'application/json',
                'X-OmniBanner-Site-Key' => $site_key,
            ));
            $args['body'] = wp_json_encode(array(
                'domain' => wp_parse_url(home_url(), PHP_URL_HOST),
                'site_url' => home_url(),
                'site_name' => get_bloginfo('name'),
                'wp_version' => get_bloginfo('version'),
                'plugin_version' => OMNIBANNER_VERSION,
            ));
            $res = wp_remote_post($api_url . '/api/sites/heartbeat', $args);
            if (!is_wp_error($res) && wp_remote_retrieve_response_code($res) === 200) {
                $data = json_decode(wp_remote_retrieve_body($res), true);
                if (is_array($data)) {
                    if (isset($data['settings']) && is_array($data['settings'])) {
                        update_option('omnibanner_site_config', wp_json_encode($data['settings']));
                    }
                    $branding = $this->apply_remote_branding($api_url, isset($data['branding']) ? $data['branding'] : array());
                    wp_send_json_success($branding);
                }
            }
        }

        $response = wp_remote_get($api_url . '/api/public/info', $this->http_args(10));
        if (is_wp_error($response)) {
            wp_send_json_error(array('message' => $response->get_error_message()));
        }
        $code = wp_remote_retrieve_response_code($response);
        if ($code !== 200) {
            wp_send_json_error(array('message' => 'HTTP ' . $code));
        }

        $data = json_decode(wp_remote_retrieve_body($response), true);
        if (!is_array($data) || !isset($data['name'])) {
            wp_send_json_error(array('message' => __('Invalid branding payload.', 'omnibanner')));
        }

        $branding = $this->apply_remote_branding($api_url, $data);
        wp_send_json_success($branding);
    }

    public function ajax_disconnect_site() {
        check_ajax_referer('omnibanner_admin_nonce', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_send_json_error(array('message' => __('Unauthorized access.', 'omnibanner')));
        }

        update_option('omnibanner_api_url', '');
        update_option('omnibanner_site_id', 0);
        update_option('omnibanner_site_key', '');
        update_option('omnibanner_site_config', '{}');
        wp_send_json_success(array('message' => __('Disconnected from OmniBanner server.', 'omnibanner')));
    }

    public function ajax_run_diagnostics() {
        check_ajax_referer('omnibanner_admin_nonce', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_send_json_error(array('message' => __('Unauthorized access.', 'omnibanner')));
        }

        $api_url = isset($_POST['api_url']) ? $this->sanitize_api_url(wp_unslash($_POST['api_url'])) : '';
        if (empty($api_url)) {
            wp_send_json_error(array('message' => __('Empty API URL.', 'omnibanner')));
        }

        $report = array();
        $domain = wp_parse_url(home_url(), PHP_URL_HOST);

        $checks = array(
            'conn' => $api_url . '/api/health',
            'info' => $api_url . '/api/public/info',
            'banner' => $api_url . '/api/public/banners?domain=' . rawurlencode($domain ? $domain : ''),
        );

        foreach ($checks as $key => $target_url) {
            $t0 = microtime(true);
            $res = wp_remote_get($target_url, $this->http_args(6));
            $ms = round((microtime(true) - $t0) * 1000);
            if (is_wp_error($res)) {
                $report[$key] = array('status' => 'failed', 'message' => $res->get_error_message(), 'time' => $ms);
            } else {
                $code = wp_remote_retrieve_response_code($res);
                $report[$key] = array(
                    'status' => ($code === 200) ? 'success' : 'failed',
                    'message' => ($code === 200) ? __('Active (HTTP 200)', 'omnibanner') : ('HTTP ' . $code),
                    'time' => $ms,
                );
            }
        }

        wp_send_json_success($report);
    }

    public function ajax_flush_cache() {
        check_ajax_referer('omnibanner_admin_nonce', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_send_json_error(array('message' => __('Unauthorized access.', 'omnibanner')));
        }

        update_option('omnibanner_cached_name', 'OmniBanner');
        update_option('omnibanner_cached_icon', '');
        update_option('omnibanner_cached_color', '#8b5cf6');
        wp_send_json_success(array('message' => __('Cached branding reset to defaults.', 'omnibanner')));
    }
}

new OmniBannerPlugin();
