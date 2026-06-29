<?php
/**
 * Plugin Name: OmniBanner Notice Integration
 * Description: Connects to a central OmniBanner service to dynamically display scheduled white-labeled alert banners.
 * Version: 1.0.0
 * Author: Antigravity
 * License: GPL2
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
        
        // AJAX hook to refresh branding configuration
        add_action('wp_ajax_omnibanner_refresh_branding', array($this, 'ajax_refresh_branding'));
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
}

new OmniBannerPlugin();
