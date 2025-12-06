import re
import yaml
import threading
from flask import Flask, request, abort, Response, jsonify
from lxml import etree
import os
from typing import Dict, Any, Optional, Tuple
import logging
import sys
from forward_proxy import ONVIFForwardProxy
from request_modifiers import ONVIFRequestModifier
from response_modifiers import ONVIFResponseModifier

DEBUG = False

# Logging
LOG_LEVEL = logging.DEBUG if DEBUG else logging.INFO

# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def create_onvif_proxy_server(all_camera_configs):
    """
    Creates a Flask app that serves as the ONVIF proxy server for all cameras.
    Each camera will have its own endpoint under /onvif/<camera_name>/.
    """
    app = Flask(__name__)
    if not isinstance(all_camera_configs, dict) or 'proxy_server_ip' not in all_camera_configs or 'proxy_server_port' not in all_camera_configs:
        raise RuntimeError('Configuration must provide a root-level "proxy_server_ip" and proxy_server_port keys')
    camera_configs = all_camera_configs.get('cameras', [])
    proxy_server_ip = all_camera_configs.get('proxy_server_ip')
    proxy_server_port = all_camera_configs.get('proxy_server_port')
    
        # Endpoint to update camera multipliers and persist to config
    @app.route('/update', methods=['POST'])
    def update_camera_config():
        # Accept JSON payload with x_multiplier and/or y_multiplier
        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400

        changed = False
        # find which camera to update: prefer explicit proxy_port or name in payload
        target_cam = None
        if 'proxy_port' in data:
            # match by proxy_port
            for cam in all_camera_configs.get('cameras', []):
                if str(cam.get('proxy_port')) == str(data.get('proxy_port')):
                    target_cam = cam
                    break
        if target_cam is None and 'name' in data:
            for cam in all_camera_configs.get('cameras', []):
                if str(cam.get('name')) == str(data.get('name')):
                    target_cam = cam
                    break

        # validate values: must be numeric and between -1 and 1
        for key in ('x_multiplier', 'y_multiplier'):
            if key in data:
                try:
                    val = float(data[key])
                except Exception:
                    return jsonify({"error": f"{key} must be a number"}), 400
                if val < -1 or val > 1:
                    return jsonify({"error": f"{key} must be between -1 and 1"}), 400
                target_cam[key] = val
                changed = True

        if changed:
            # We only update the in-memory camera entry here.
            # Persisting changes to disk is intentionally left to the user.
            cam_name = target_cam.get('name', '<unknown>')
            return jsonify({
                "status": "ok",
                "message": f"Updated in memory for camera '{cam_name}'. To make this change permanent, edit config/cameras.yaml and restart the proxy."
            }), 200

        return jsonify({"status": "no_changes", "message": "No updates provided"}), 200

    @app.route('/status.json', methods=['GET'])
    def status_json():
        # Return a lightweight JSON of current camera statuses for polling
        try:
            cam_list = all_camera_configs.get('cameras') 
            out = []
            for cam in cam_list:
                thread_obj = cam.get('_thread')
                is_running = False
                try:
                    if thread_obj is not None:
                        is_running = bool(getattr(thread_obj, 'is_alive', lambda: False)())
                except Exception:
                    is_running = False
                out.append({
                    'name': cam.get('name'),
                    'proxy_port': cam.get('proxy_port'),
                    'status': cam.get('status', 'IDLE'),
                    'messages_proxied': cam.get('_messages_proxied', 0),
                    'x_multiplier': cam.get('x_multiplier'),
                    'y_multiplier': cam.get('y_multiplier'),
                    'move_timeout': cam.get('move_timeout'),
                    'is_running': is_running,
                })
            return jsonify({'cameras': out})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # Root status page
    @app.route('/')
    def status_page():
        # Render a nicer HTML status page with cards and example Frigate snippet
        cam_list = all_camera_configs.get('cameras') 
        global_host = all_camera_configs.get('proxy_server_ip') if isinstance(all_camera_configs, dict) else '127.0.0.1'

        css = '''
        body { font-family: Arial, sans-serif; background: #f7f9fb; color: #222; }
        .container { max-width: 1100px; margin: 24px auto; }
        h1 { margin-bottom: 8px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
        .card { background: white; border: 1px solid #e1e6ea; border-radius: 8px; padding: 12px 16px; box-shadow: 0 1px 2px rgba(16,24,40,0.03); }
        .card h2 { margin: 0 0 8px 0; font-size: 18px; }
        .meta { font-size: 13px; color: #4b5563; margin-bottom: 8px; }
        .example { margin-top: 15px; font-size: 15px; font-weight: bold; }
        .status { display: inline-block; padding: 4px 8px; border-radius: 999px; font-weight: 600; font-size: 12px; }
        .status.idle { background: #eef2ff; color: #4f46e5; }
        .status.moving { background: #fff7ed; color: #b45309; }
        .status.running { background: #eef2ff; color: #4f46e5; }
        .status.stopped { background: #fff7ed; color: #b45309; }
        pre.snippet { background: #0b1220; color: #cbd5e1; padding: 12px; border-radius: 6px; overflow: auto; }
        .footer { font-size: 13px; color: #6b7280; }
        '''

        cards = []
        for cam in cam_list:
            name = cam.get('name')
            port = cam.get('proxy_port')
            proxy_url = f"http://{global_host}:{port}/onvif/"
            target = f"{cam.get('camera_host')}:{port}"
            status = cam.get('status', 'IDLE')
            status_class = 'moving' if status.upper() == 'MOVING' else 'idle'
            move_timeout = cam.get('move_timeout', '10')
            # Thread running status (if main attached a thread object to the camera config)
            thread_obj = cam.get('_thread')
            is_running = False
            try:
                if thread_obj is not None:
                    is_running = bool(getattr(thread_obj, 'is_alive', lambda: False)())
            except Exception:
                is_running = False
            running_class = 'running' if is_running else 'stopped'
            running_text = 'Running' if is_running else 'Stopped'
            messages_proxied = cam.get('_messages_proxied', 0)
            x_val = cam.get('x_multiplier', '')
            y_val = cam.get('y_multiplier', '')
            card = f'''
            <div class="card">
                <h2>{name}</h2>
                <div class="meta">Proxy URL: <a href="{proxy_url}">{proxy_url}</a></div>
                <div class="meta">Camera target: {target}</div>
                <div class="meta">Thread: <span id="thread_{port}" class="status {running_class}">{running_text}</span></div>
                <div class="meta">Status: <span id="status_{port}" class="status {status_class}">{status}</span></div>
                <div class="meta">Messages proxied: <strong id="messages_{port}">{messages_proxied}</strong></div>
                <details>
                <summary class="example">Settings</summary>
                <div class="meta">X multiplier: <input id="x_{port}" type="text" value="{x_val}" /></div>
                <div class="meta">Y multiplier: <input id="y_{port}" type="text" value="{y_val}" /></div>
                <div class="meta">Move timeout: <input id="move_timeout_{port}" type="text" value="{move_timeout}" /></div>
                <div class="meta">
                    <button id="btn_{port}" onclick="updateMultipliers('{port}')">Save</button>
                    <span id="msg_{port}" class="footer"></span>
                </div>
                </details>
                <details>
                <summary class="example">Example Frigate Config:</summary>
                <p class="footer">Add this to your Frigate config.yml under the camera's config.</p>
                                <pre class="snippet">
cameras:
    {name}:
        onvif:
            host: {global_host}
            port: {port}
            username: camera_user
            password: camera_pass             
                                </pre>
                                </details>
                        </div>
                        '''
            cards.append(card)
        html = """
        <html>
        <head>
            <title>ONVIF Proxy Status</title>
            <style>__CSS__</style>
            <script>
            function updateMultipliers(port) {
                var btn = document.getElementById('btn_' + port);
                var el = document.getElementById('msg_' + port);
                var xraw = document.getElementById('x_' + port).value;
                var yraw = document.getElementById('y_' + port).value;
                var moveTimeoutRaw = document.getElementById('move_timeout_' + port).value;

                // Build payload only for non-empty fields
                var payload = {};
                if (xraw !== '') {
                    var xv = parseFloat(xraw);
                    if (!isFinite(xv) || xv < -1 || xv > 1) {
                        el.innerText = 'X must be a number between -1 and 1';
                        return;
                    }
                    payload.x_multiplier = xv;
                }
                if (yraw !== '') {
                    var yv = parseFloat(yraw);
                    if (!isFinite(yv) || yv < -1 || yv > 1) {
                        el.innerText = 'Y must be a number between -1 and 1';
                        return;
                    }
                    payload.y_multiplier = yv;
                }
                if (moveTimeoutRaw !== '') {
                    var moveTimeoutVal = parseInt(moveTimeoutRaw, 10);
                    if (!isFinite(moveTimeoutVal) || moveTimeoutVal < 0) {
                        el.innerText = 'Move timeout must be a non-negative integer';
                        return;
                    }
                    payload.move_timeout = moveTimeoutVal;
                }

                if (Object.keys(payload).length === 0) {
                    el.innerText = 'No changes to save';
                    setTimeout(function(){ el.innerText = ''; }, 1500);
                    return;
                }

                // Disable button while saving
                btn.disabled = true;
                el.innerText = 'Saving...';

                fetch('/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(Object.assign({proxy_port: port}, payload))
                }).then(function(resp) {
                    return resp.json().then(function(j){ return {ok: resp.ok, json: j}; });
                }).then(function(r) {
                    if (r.ok) {
                        el.innerText = r.json && r.json.message ? r.json.message : 'Saved';
                        setTimeout(function(){ el.innerText = ''; }, 4000);
                    } else {
                        el.innerText = r.json && r.json.error ? r.json.error : 'Error saving';
                    }
                }).catch(function(e) {
                    el.innerText = 'Network error';
                }).finally(function() { btn.disabled = false; });
            }
            
            // Poll the server for up-to-date camera statuses and message counts
            function pollStatus() {
                fetch('/status.json', {cache: 'no-store'})
                    .then(function(resp){ return resp.json(); })
                    .then(function(data){
                        if (!data || !data.cameras) return;
                        data.cameras.forEach(function(c){
                            try {
                                var port = c.proxy_port;
                                var statusEl = document.getElementById('status_' + port);
                                if (statusEl) {
                                    statusEl.innerText = c.status;
                                    // update status classes
                                    statusEl.className = 'status ' + (c.status && c.status.toUpperCase() === 'MOVING' ? 'moving' : 'idle');
                                }
                                var threadEl = document.getElementById('thread_' + port);
                                if (threadEl) {
                                    var runningClass = c.is_running ? 'running' : 'stopped';
                                    threadEl.innerText = c.is_running ? 'Running' : 'Stopped';
                                    threadEl.className = 'status ' + runningClass;
                                }
                                var messagesEl = document.getElementById('messages_' + port);
                                if (messagesEl) {
                                    messagesEl.innerText = c.messages_proxied || 0;
                                }
                            } catch (e) { /* ignore */ }
                        });
                    }).catch(function(){ /* ignore errors during polling */ });
            }

            // start polling every 2 seconds
            setInterval(pollStatus, 2000);
            // initial poll
            setTimeout(pollStatus, 500);
            </script>
        </head>
        <body>
            <div class="container">
                <h1>ONVIF Proxy Status</h1>
                <p class="footer">Proxy host: <strong>__HOST__</strong></p>
                <div class="grid">
                    __CARDS__
                </div>
            </div>
        </body>
        </html>
        """
        # inject variables into the non-f string to avoid escaping braces in CSS/JS
        html = html.replace('__CSS__', css).replace('__CARDS__', ''.join(cards)).replace('__HOST__', global_host)

        return html
    return app

def create_onvif_proxy_app(camera_config, all_camera_configs=None):
    """
    Creates a Flask app for a specific camera config.
    all_camera_configs: list of all camera configs, for status page.
    """
    app = Flask(__name__)
    CAMERA_NAME = camera_config['name']
    CAMERA_HOST = camera_config['camera_host']
    CAMERA_PORT = camera_config['camera_port']
    # Use global proxy_server_ip (root-level) from provided all_camera_configs
    if not isinstance(all_camera_configs, dict) or 'proxy_server_ip' not in all_camera_configs:
        raise RuntimeError('Configuration must provide a root-level "proxy_server_ip" key')
    proxy_server_ip = all_camera_configs.get('proxy_server_ip', '127.0.0.1')
    PROXY_PORT = camera_config['proxy_port']

    def debug(msg, payload_str):
        if DEBUG:
            logger.debug(f"[{camera_config['name']}] {msg} \n{payload_str}")
            #print(f"[{camera_config['name']}] {msg} \n{payload_str[:500]}...")

    def parse_soap_request(soap_body: str) -> Tuple[Optional[str], Optional[etree.Element]]:
        """
        Parse SOAP request and extract operation name.

        Args:
            soap_body: Raw SOAP XML string

        Returns:
            Tuple of (operation_name, xml_root)
        """
        try:
            root = etree.fromstring(soap_body.encode())

            # Find the operation (first element in Body)
            body = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Body')
            if body is None:
                body = root.find('.//{http://schemas.xmlsoap.org/soap/envelope/}Body')

            if body is not None and len(body) > 0:
                operation_elem = body[0]
                operation = etree.QName(operation_elem).localname
                return operation, root

            return None, root

        except Exception as e:
            logger.error(f"[{camera_config['name']}] Error parsing SOAP request: {e}")
            return None, None

    # ONVIF proxy endpoint under /onvif/
    @app.route('/onvif/<service>', methods=['POST'])
    def handle_onvif_request(service):
        # Get SOAP body
        soap_body = request.data.decode('utf-8')
        #debug("Raw Client Request Payload:", soap_body)
        
        # Parse SOAP request
        operation, root = parse_soap_request(soap_body)
        logger.info(f"[{camera_config['name']}] Received {operation} request for service {service}")

        modified_request_body = ONVIFRequestModifier.modify_onvif_request(camera_config, operation, root)
        debug("Modified Request Payload:", modified_request_body)
        logger.info(f"[{camera_config['name']}] Camera status: {camera_config.get('status', 'IDLE')}")
        logger.info(f"[{camera_config['name']}] Proxying {operation} to camera")
        response_text, status_code = ONVIFForwardProxy.proxy_tcp_request(camera_config, service, modified_request_body)
        debug("Camera Response Payload:", response_text)
        
        global_host = all_camera_configs.get('proxy_server_ip') if isinstance(all_camera_configs, dict) else '127.0.0.1'
        response_text = ONVIFResponseModifier.rewrite_host_urls(global_host, camera_config, response_text)

        final_response_body = ONVIFResponseModifier.modify_onvif_response(camera_config, operation, etree.fromstring(response_text.encode()))
        debug("Final Response Payload:", final_response_body)
        
        # Increment message counter
        if '_messages_proxied' not in camera_config:
            camera_config['_messages_proxied'] = 0
        camera_config['_messages_proxied'] += 1
        
        return Response(final_response_body, mimetype='application/soap+xml; charset=utf-8')
    return app

def load_camera_configs(config_path):
    """
    Load camera configuration from YAML. Expects a dict with keys:
      - proxy_server_ip: string
      - cameras: list of camera entries

    Returns the parsed dict.
    """
    if not os.path.exists(config_path):
        example_path = os.path.join(os.path.dirname(config_path), 'cameras.yaml.example')
        raise RuntimeError(
            f"Configuration file '{config_path}' not found.\n"
            f"Create it by copying the example and editing it:\n"
            f"  cp {example_path} {config_path}\n"
            f"Then edit '{config_path}' to configure your cameras and proxy_server_ip."
        )

    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise RuntimeError('Invalid config format: expected a YAML mapping at top level')
    if 'cameras' not in data or not isinstance(data['cameras'], list):
        raise RuntimeError("Invalid config: 'cameras' key missing or not a list")
    return data

def run_flask_app_for_server(all_camera_configs):
    app = create_onvif_proxy_server(all_camera_configs)
    camera_configs = all_camera_configs.get('cameras', [])
    proxy_server_ip = all_camera_configs.get('proxy_server_ip', '0.0.0.0')
    proxy_server_port = all_camera_configs.get('proxy_server_port', 80)

    logger.info(f"[ONVIF Proxy Server] Starting ONVIF Proxy server on http://{proxy_server_ip}:{proxy_server_port}")
    logger.info(f"[ONVIF Proxy Server] Managing {len(camera_configs)} cameras.")

    app.run(host="0.0.0.0", port=proxy_server_port, threaded=True)
                                             
def run_flask_app_for_camera(camera_config, all_camera_configs):
    app = create_onvif_proxy_app(camera_config, all_camera_configs)
    # Bind host comes from root-level proxy_server_ip
    if not isinstance(all_camera_configs, dict) or 'proxy_server_ip' not in all_camera_configs:
        raise RuntimeError('Configuration must provide a root-level "proxy_server_ip" key')
    bind_host = all_camera_configs.get('proxy_server_ip')
    port = camera_config['proxy_port']
    logger.info(f"[{camera_config['name']}] Starting ONVIF Proxy server on http://{bind_host}:{port}")
    logger.info(f"[{camera_config['name']}] Forwarding requests to camera at {camera_config['camera_host']}:{camera_config['camera_port']}")
    app.run(host="0.0.0.0", port=port, threaded=True)

if __name__ == '__main__':
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'cameras.yaml')
    cfg = load_camera_configs(config_path)
    # store config path and a lock so worker threads can persist updates safely
    cfg['_config_path'] = config_path
    cfg['_file_lock'] = threading.Lock()
    camera_configs = cfg['cameras']

    main_thread = threading.Thread(target=run_flask_app_for_server, args=(cfg,), daemon=True)
    main_thread.start()

    camera_threads = []
    for cam_cfg in camera_configs:
        t = threading.Thread(target=run_flask_app_for_camera, args=(cam_cfg, cfg), daemon=True)
        # Attach thread object to config so status page can show if it's alive
        cam_cfg['_thread'] = t
        t.start()
        camera_threads.append(t)
    # Keep main thread alive
    try:
        while True:
            for t in camera_threads:
                t.join(1)
    except KeyboardInterrupt:
        print("Shutting down all ONVIF proxy servers...")

