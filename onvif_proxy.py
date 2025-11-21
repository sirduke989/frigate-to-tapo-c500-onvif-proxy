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
            logger.error(f"Error parsing SOAP request: {e}")
            return None, None

    # ONVIF proxy endpoint under /onvif/
    @app.route('/onvif/<service>', methods=['POST'])
    def handle_onvif_request(service):
        # Get SOAP body
        soap_body = request.data.decode('utf-8')
        #debug("Raw Client Request Payload:", soap_body)
        
        # Parse SOAP request
        operation, root = parse_soap_request(soap_body)
        logger.info(f"Received {operation} request for service {service}")

        modified_request_body = ONVIFRequestModifier.modify_onvif_request(camera_config, operation, root)
        debug("Modified Request Payload:", modified_request_body)
        logger.info(f"Camera status: {camera_config.get('status', 'IDLE')}")

        logger.info(f"Proxying {operation} to camera")
        response_text, status_code = ONVIFForwardProxy.proxy_tcp_request(camera_config, service, modified_request_body)
        debug("Camera Response Payload:", response_text)
        
        response_text = ONVIFResponseModifier.rewrite_host_urls(camera_config, response_text)

        final_response_body = ONVIFResponseModifier.modify_onvif_response(camera_config, operation, etree.fromstring(response_text.encode()))
        debug("Final Response Payload:", final_response_body)
        
        return Response(final_response_body, mimetype='application/soap+xml; charset=utf-8')

    # Root status page
    @app.route('/')
    def status_page():
        # Compose HTML status for all cameras (if available)
        html = ['<html><head><title>ONVIF Proxy Status</title></head><body>']
        html.append(f'<h1>ONVIF Proxy Status - {camera_config["name"]}</h1>')
        html.append('<ul>')
        # If all_camera_configs is provided, show all, else just this one
        cam_list = all_camera_configs.get('cameras') if isinstance(all_camera_configs, dict) else [camera_config]
        global_host = all_camera_configs.get('proxy_host') if isinstance(all_camera_configs, dict) else '127.0.0.1'
        for cam in cam_list:
            url = f"http://{global_host}:{cam['proxy_port']}/onvif/"
            html.append(f'<li><b>{cam["name"]}</b>: <a href="{url}">{url}</a> &rarr; {cam["camera_host"]}:{cam["camera_port"]}</li>')
        html.append('</ul>')
        html.append('</body></html>')
        return '\n'.join(html)

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
    logger.info(f"Starting ONVIF Proxy server for '{camera_config['name']}' on http://{bind_host}:{port}")
    logger.info(f"Forwarding requests to camera at {camera_config['camera_host']}:{camera_config['camera_port']}")
    app.run(host="0.0.0.0", port=port, threaded=True)

if __name__ == '__main__':
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'cameras.yaml')
    cfg = load_camera_configs(config_path)
    camera_configs = cfg['cameras']

    threads = []
    for cam_cfg in camera_configs:
        t = threading.Thread(target=run_flask_app_for_camera, args=(cam_cfg, cfg), daemon=True)
        t.start()
        threads.append(t)
    # Keep main thread alive
    try:
        while True:
            for t in threads:
                t.join(1)
    except KeyboardInterrupt:
        print("Shutting down all ONVIF proxy servers...")

