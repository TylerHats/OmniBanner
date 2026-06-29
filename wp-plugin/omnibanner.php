<?php
/**
 * Plugin Name: OmniBanner Notice Integration
 * Description: Connects to a central OmniBanner service to dynamically display scheduled white-labeled alert banners.
 * Version: 1.0.0
 * Author: Tyler Hatfield
 * License: GPL3+
 * Text Domain: omnibanner
 */

if (!defined('ABSPATH')) {
    exit; // Exit if accessed directly
}

class OmniBannerPlugin {
    
    public function __construct() {
        // Admin hooks
        add_action('admin_menu', array($this, 'add_admin_menu'));
        add_action('admin_init', array($this, 'register_settings'));
        add_action('admin_enqueue_scripts', array($this, 'enqueue_admin_assets'));
        
        // Frontend hooks
        add_action('wp_enqueue_scripts', array($this, 'enqueue_frontend_assets'));
        
        // AJAX hooks
        add_action('wp_ajax_omnibanner_refresh_branding', array($this, 'ajax_refresh_branding'));
        add_action('wp_ajax_omnibanner_run_diagnostics', array($this, 'ajax_run_diagnostics'));
        add_action('wp_ajax_omnibanner_flush_cache', array($this, 'ajax_flush_cache'));
    }


    public function add_admin_menu() {
        // Fetch cached brand name
        $app_name = get_option('omnibanner_cached_name', 'OmniBanner');
        
        add_options_page(
            $app_name . ' Settings',
            $app_name,
            'manage_options',
            'omnibanner-settings',
            array($this, 'render_settings_page')
        );
    }

    public function register_settings() {
        register_setting('omnibanner_options', 'omnibanner_api_url', array(
            'type' => 'string',
            'sanitize_callback' => 'esc_url_raw',
            'default' => ''
        ));
        register_setting('omnibanner_options', 'omnibanner_cached_name');
        register_setting('omnibanner_options', 'omnibanner_cached_icon');
        register_setting('omnibanner_options', 'omnibanner_cached_color');
    }

    public function enqueue_admin_assets($hook) {
        if ($hook !== 'settings_page_omnibanner-settings') {
            return;
        }

        wp_enqueue_style('omnibanner-admin-css', plugin_dir_url(__FILE__) . 'assets/admin.css', array(), '1.0.0');
        wp_enqueue_script('omnibanner-admin-js', plugin_dir_url(__FILE__) . 'assets/admin.js', array('jquery'), '1.0.0', true);
        
        // Localize variables
        wp_localize_script('omnibanner-admin-js', 'omnibanner_admin', array(
            'ajax_url' => admin_url('admin-ajax.php'),
            'nonce' => wp_create_nonce('omnibanner_admin_nonce')
        ));
    }

    public function enqueue_frontend_assets() {
        $api_url = get_option('omnibanner_api_url');
        if (empty($api_url)) {
            return; // Config is incomplete
        }

        wp_enqueue_style('omnibanner-frontend-css', plugin_dir_url(__FILE__) . 'assets/banner.css', array(), '1.0.0');
        wp_enqueue_script('omnibanner-frontend-js', plugin_dir_url(__FILE__) . 'assets/banner.js', array(), '1.0.0', true);
        
        // Fetch clean domain identifier (host)
        $domain = parse_url(home_url(), PHP_URL_HOST);
        
        // Localize script to load settings dynamically in JavaScript
        wp_localize_script('omnibanner-frontend-js', 'OmniBannerConf', array(
            'api_url' => esc_url($api_url),
            'domain' => esc_attr($domain)
        ));
    }

    public function render_settings_page() {
        $api_url = get_option('omnibanner_api_url', '');
        $app_name = get_option('omnibanner_cached_name', 'OmniBanner');
        $app_icon = get_option('omnibanner_cached_icon', '');
        $app_color = get_option('omnibanner_cached_color', '#8b5cf6');
        
        // Fallback logo if empty
        if (empty($app_icon)) {
            $app_icon = plugin_dir_url(__FILE__) . 'assets/logo.png'; // default local fallback icon
        }
        
        ?>
        <div class="wrap omnibanner-admin-wrap" style="--brand-color: <?php echo esc_attr($app_color); ?>;">
            <div class="omnibanner-header">
                <?php if ($app_icon): ?>
                    <img src="<?php echo esc_url($app_icon); ?>" alt="App Logo" class="omnibanner-brand-icon">
                <?php endif; ?>
                <h1 class="omnibanner-title"><?php echo esc_html($app_name); ?> Settings</h1>
            </div>

            <div class="card omnibanner-card">
                <h2 class="title">Connection parameters</h2>
                <p>Input the endpoint URL of your deployed OmniBanner service. The plugin will connect to fetch scheduled notification banners and branding details.</p>
                
                <form method="post" action="options.php" id="omnibanner-config-form">
                    <?php settings_fields('omnibanner_options'); ?>
                    <?php do_settings_sections('omnibanner_options'); ?>
                    
                    <table class="form-table">
                        <tr valign="top">
                            <th scope="row">OmniBanner URL / IP</th>
                            <td>
                                <input type="url" name="omnibanner_api_url" id="omnibanner-api-url" value="<?php echo esc_attr($api_url); ?>" class="regular-text" placeholder="https://omnibanner.company.com" required />
                                <button type="button" class="button button-secondary" id="omnibanner-sync-btn">⚡ Sync Branding</button>
                                <p class="description">Enter the base URL where the central service is running (e.g. <code>http://127.0.0.1:8000</code>)</p>
                            </td>
                        </tr>
                    </table>
                    
                    <!-- Hidden settings populated dynamically by AJAX branding sync -->
                    <input type="hidden" name="omnibanner_cached_name" id="omnibanner-cached-name" value="<?php echo esc_attr($app_name); ?>" />
                    <input type="hidden" name="omnibanner_cached_icon" id="omnibanner-cached-icon" value="<?php echo esc_url($app_icon); ?>" />
                    <input type="hidden" name="omnibanner_cached_color" id="omnibanner-cached-color" value="<?php echo esc_attr($app_color); ?>" />

                    <?php submit_button('Save Settings'); ?>
                </form>
            </div>

            <div class="card omnibanner-card" style="margin-top: 20px;">
                <h2 class="title">Integration Diagnostics</h2>
                <p>Verify network configurations and test connection response limits with the central service.</p>
                
                <table class="widefat fixed striped" style="margin-top: 15px; margin-bottom: 15px; border-radius: 4px; overflow: hidden;">
                    <thead>
                        <tr>
                            <th style="width: 250px; font-weight:600; padding: 10px;">Diagnostic Test Item</th>
                            <th style="font-weight:600; padding: 10px;">Check Status</th>
                            <th style="width: 100px; font-weight:600; text-align: right; padding: 10px;">Latency</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td style="padding: 10px;"><strong>Endpoint Connectivity check</strong></td>
                            <td id="diag-status-conn" style="padding: 10px;"><span class="diag-badge gray">Not Tested</span></td>
                            <td id="diag-time-conn" style="text-align: right; color: #888; padding: 10px;">-</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px;"><strong>Branding Information query</strong></td>
                            <td id="diag-status-info" style="padding: 10px;"><span class="diag-badge gray">Not Tested</span></td>
                            <td id="diag-time-info" style="text-align: right; color: #888; padding: 10px;">-</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px;"><strong>Active Banner endpoint ping</strong></td>
                            <td id="diag-status-banner" style="padding: 10px;"><span class="diag-badge gray">Not Tested</span></td>
                            <td id="diag-time-banner" style="text-align: right; color: #888; padding: 10px;">-</td>
                        </tr>
                    </tbody>
                </table>
                
                <div style="display: flex; gap: 10px;">
                    <button type="button" class="button button-primary" id="omnibanner-run-diag-btn">🔬 Run Diagnostics Suite</button>
                    <button type="button" class="button button-secondary" id="omnibanner-flush-btn">🗑️ Flush Cached Assets</button>
                </div>
            </div>
        </div>
        <?php
    }

    public function ajax_refresh_branding() {
        check_ajax_referer('omnibanner_admin_nonce', 'nonce');
        
        if (!current_user_can('manage_options')) {
            wp_send_json_error(array('message' => 'Unauthorized user access'));
        }

        $api_url = isset($_POST['api_url']) ? esc_url_raw($_POST['api_url']) : '';
        if (empty($api_url)) {
            wp_send_json_error(array('message' => 'Empty API URL endpoint'));
        }

        // Fetch brand settings from public endpoint `/api/public/info`
        $fetch_url = rtrim($api_url, '/') . '/api/public/info';
        $response = wp_remote_get($fetch_url, array('timeout' => 10, 'sslverify' => false));

        if (is_wp_error($response)) {
            wp_send_json_error(array('message' => 'HTTP request failed: ' . $response->get_error_message()));
        }

        $code = wp_remote_retrieve_response_code($response);
        if ($code !== 200) {
            wp_send_json_error(array('message' => 'Service returned status ' . $code));
        }

        $body = wp_remote_retrieve_body($response);
        $data = json_decode($body, true);

        if (!$data || !isset($data['name'])) {
            wp_send_json_error(array('message' => 'Invalid branding response payload format'));
        }

        // Handle image mapping if logo icon path is relative
        $icon_url = $data['icon_url'];
        if (strpos($icon_url, 'http') !== 0) {
            // Prepend service API URL base
            $icon_url = rtrim($api_url, '/') . '/' . ltrim($icon_url, '/');
        }

        // Update local options immediately
        update_option('omnibanner_cached_name', sanitize_text_field($data['name']));
        update_option('omnibanner_cached_icon', esc_url_raw($icon_url));
        update_option('omnibanner_cached_color', sanitize_hex_color($data['color']));

        wp_send_json_success(array(
            'name' => sanitize_text_field($data['name']),
            'icon_url' => esc_url_raw($icon_url),
            'color' => sanitize_hex_color($data['color'])
        ));
    }

    public function ajax_run_diagnostics() {
        check_ajax_referer('omnibanner_admin_nonce', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_send_json_error(array('message' => 'Unauthorized access'));
        }

        $api_url = isset($_POST['api_url']) ? esc_url_raw($_POST['api_url']) : '';
        if (empty($api_url)) {
            wp_send_json_error(array('message' => 'Empty API URL'));
        }

        $report = array();
        
        // 1. Connection check
        $t1 = microtime(true);
        $res1 = wp_remote_get(rtrim($api_url, '/'), array('timeout' => 5, 'sslverify' => false));
        $d1 = round((microtime(true) - $t1) * 1000);
        if (is_wp_error($res1)) {
            $report['conn'] = array('status' => 'failed', 'message' => $res1->get_error_message(), 'time' => $d1);
        } else {
            $code = wp_remote_retrieve_response_code($res1);
            $report['conn'] = array('status' => 'success', 'message' => 'Connected (HTTP ' . $code . ')', 'time' => $d1);
        }

        // 2. Info Check
        $t2 = microtime(true);
        $res2 = wp_remote_get(rtrim($api_url, '/') . '/api/public/info', array('timeout' => 5, 'sslverify' => false));
        $d2 = round((microtime(true) - $t2) * 1000);
        if (is_wp_error($res2)) {
            $report['info'] = array('status' => 'failed', 'message' => $res2->get_error_message(), 'time' => $d2);
        } else {
            $code = wp_remote_retrieve_response_code($res2);
            if ($code === 200) {
                $report['info'] = array('status' => 'success', 'message' => 'Active', 'time' => $d2);
            } else {
                $report['info'] = array('status' => 'failed', 'message' => 'HTTP ' . $code, 'time' => $d2);
            }
        }

        // 3. Banner Check
        $t3 = microtime(true);
        $domain = parse_url(home_url(), PHP_URL_HOST);
        $res3 = wp_remote_get(rtrim($api_url, '/') . '/api/public/banner?domain=' . esc_attr($domain), array('timeout' => 5, 'sslverify' => false));
        $d3 = round((microtime(true) - $t3) * 1000);
        if (is_wp_error($res3)) {
            $report['banner'] = array('status' => 'failed', 'message' => $res3->get_error_message(), 'time' => $d3);
        } else {
            $code = wp_remote_retrieve_response_code($res3);
            if ($code === 200) {
                $report['banner'] = array('status' => 'success', 'message' => 'Active', 'time' => $d3);
            } else {
                $report['banner'] = array('status' => 'failed', 'message' => 'HTTP ' . $code, 'time' => $d3);
            }
        }

        wp_send_json_success($report);
    }

    public function ajax_flush_cache() {
        check_ajax_referer('omnibanner_admin_nonce', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_send_json_error(array('message' => 'Unauthorized access'));
        }

        update_option('omnibanner_cached_name', 'OmniBanner');
        update_option('omnibanner_cached_icon', '');
        update_option('omnibanner_cached_color', '#8b5cf6');

        wp_send_json_success(array('message' => 'Cache flushed successfully'));
    }
}

new OmniBannerPlugin();

