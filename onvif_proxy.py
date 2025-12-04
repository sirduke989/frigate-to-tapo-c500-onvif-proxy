import re
import yaml
import threading
from flask import Flask, request, abort, Response
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

def create_onvif_proxy_app(camera_config, all_camera_configs=None):
    """
    Creates a Flask app for a specific camera config.
    all_camera_configs: list of all camera configs, for status page.
    """
    app = Flask(__name__)
    CAMERA_NAME = camera_config['name']
    CAMERA_HOST = camera_config['camera_host']
    CAMERA_PORT = camera_config['camera_port']
    # Use global proxy_host (root-level) from provided all_camera_configs
    if not isinstance(all_camera_configs, dict) or 'proxy_host' not in all_camera_configs:
        raise RuntimeError('Configuration must provide a root-level "proxy_host" key')
    PROXY_HOST = all_camera_configs.get('proxy_host', '127.0.0.1')
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
        
        global_host = all_camera_configs.get('proxy_host') if isinstance(all_camera_configs, dict) else '127.0.0.1'
        response_text = ONVIFResponseModifier.rewrite_host_urls(global_host, camera_config, response_text)

        final_response_body = ONVIFResponseModifier.modify_onvif_response(camera_config, operation, etree.fromstring(response_text.encode()))
        debug("Final Response Payload:", final_response_body)
        
        # Increment message counter
        if '_messages_proxied' not in camera_config:
            camera_config['_messages_proxied'] = 0
        camera_config['_messages_proxied'] += 1
        
        return Response(final_response_body, mimetype='application/soap+xml; charset=utf-8')

    # Root status page
    @app.route('/')
    def status_page():
        # Render a nicer HTML status page with cards and example Frigate snippet
        cam_list = all_camera_configs.get('cameras') if isinstance(all_camera_configs, dict) else [camera_config]
        global_host = all_camera_configs.get('proxy_host') if isinstance(all_camera_configs, dict) else '127.0.0.1'

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
            move_timeout = cam.get('move_timeout', '30s')
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
            card = f'''
            <div class="card">
                <h2>{name}</h2>
                <div class="meta">Proxy URL: <a href="{proxy_url}">{proxy_url}</a></div>
                <div class="meta">Camera target: {target}</div>
                <div class="meta">Move timeout: {move_timeout}</div>
                <div class="meta">Thread: <span class="status {running_class}">{running_text}</span></div>
                <div class="meta">Status: <span class="status {status_class}">{status}</span></div>
                <div class="meta">Messages proxied: <strong>{messages_proxied}</strong></div>
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
        html = f"""
        <html>
        <head>
            <title>ONVIF Proxy Status</title>
            <style>{css}</style>
        </head>
        <body>
            <div class="container">
                <h1>ONVIF Proxy Status</h1>
                <p class="footer">Proxy host: <strong>{global_host}</strong></p>
                <div class="grid">
                    {''.join(cards)}
                </div>
            </div>
        </body>
        </html>
        """

        return html
    return app

def load_camera_configs(config_path):
    """
    Load camera configuration from YAML. Expects a dict with keys:
      - proxy_host: string
      - cameras: list of camera entries

    Returns the parsed dict.
    """
    if not os.path.exists(config_path):
        example_path = os.path.join(os.path.dirname(config_path), 'cameras.yaml.example')
        raise RuntimeError(
            f"Configuration file '{config_path}' not found.\n"
            f"Create it by copying the example and editing it:\n"
            f"  cp {example_path} {config_path}\n"
            f"Then edit '{config_path}' to configure your cameras and proxy_host."
        )

    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise RuntimeError('Invalid config format: expected a YAML mapping at top level')
    if 'cameras' not in data or not isinstance(data['cameras'], list):
        raise RuntimeError("Invalid config: 'cameras' key missing or not a list")
    return data

def run_flask_app_for_camera(camera_config, all_camera_configs):
    app = create_onvif_proxy_app(camera_config, all_camera_configs)
    # Bind host comes from root-level proxy_host
    if not isinstance(all_camera_configs, dict) or 'proxy_host' not in all_camera_configs:
        raise RuntimeError('Configuration must provide a root-level "proxy_host" key')
    bind_host = all_camera_configs.get('proxy_host')
    port = camera_config['proxy_port']
    logger.info(f"[{camera_config['name']}] Starting ONVIF Proxy server on http://{bind_host}:{port}")
    logger.info(f"[{camera_config['name']}] Forwarding requests to camera at {camera_config['camera_host']}:{camera_config['camera_port']}")
    app.run(host="0.0.0.0", port=port, threaded=True)

if __name__ == '__main__':
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'cameras.yaml')
    cfg = load_camera_configs(config_path)
    camera_configs = cfg['cameras']

    threads = []
    for cam_cfg in camera_configs:
        t = threading.Thread(target=run_flask_app_for_camera, args=(cam_cfg, cfg), daemon=True)
        # Attach thread object to config so status page can show if it's alive
        cam_cfg['_thread'] = t
        t.start()
        threads.append(t)
    # Keep main thread alive
    try:
        while True:
            for t in threads:
                t.join(1)
    except KeyboardInterrupt:
        print("Shutting down all ONVIF proxy servers...")

