import requests
from requests.auth import HTTPDigestAuth
import logging
from typing import Tuple
import socket

logger = logging.getLogger(__name__)


class ONVIFForwardProxy:
    def proxy_tcp_request(camera_config, 
                          service: str, soap_body: str,
                          timeout: int = 10) -> Tuple[str, int]:
        """
        Forward SOAP request to actual camera.

        Args:
            CAMERA_HOST: Forwarding camera IP
            CAMERA_PORT: Forwarding camera port
            service: ONVIF service name
            existing_auth: ONVIF authentication passed from the initial request to the proxy
            soap_body: SOAP request body
            timeout: Request timeout in seconds

        Returns:
            Tuple of (response_text, status_code)
        """
        CAMERA_NAME = camera_config['name']
        CAMERA_HOST = camera_config['camera_host']
        CAMERA_PORT = camera_config['camera_port']

        try:
            service_url = f'http://{CAMERA_HOST}:{CAMERA_PORT}/onvif/{service}'

            # Prepare headers
            headers = {
                'Content-Type': 'application/soap+xml; charset=utf-8',
                'User-Agent': 'ONVIF-Proxy/1.0',
            }

            logger.debug(f"Forwarding to {service_url}")

            response = requests.post(
                service_url,
                data=soap_body,
                headers=headers,
                auth=None,
                timeout=timeout
            )

            return response.text, response.status_code
        except requests.exceptions.Timeout:
            logger.error(f"Timeout forwarding request to {service_url}")
            return ONVIFForwardProxy.timeout_fault(), 500

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to {service_url}: {e}")
            return ONVIFForwardProxy.connection_fault(), 500

        except Exception as e:
            logger.error(f"Error forwarding request: {e}", exc_info=True)
            return ONVIFForwardProxy.generic_fault(str(e)), 500

    def timeout_fault(self) -> str:
        """Generate SOAP fault for timeout."""
        return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
    <SOAP-ENV:Body>
        <SOAP-ENV:Fault>
            <SOAP-ENV:Code>
                <SOAP-ENV:Value>SOAP-ENV:Receiver</SOAP-ENV:Value>
            </SOAP-ENV:Code>
            <SOAP-ENV:Reason>
                <SOAP-ENV:Text xml:lang="en">Request timeout</SOAP-ENV:Text>
            </SOAP-ENV:Reason>
        </SOAP-ENV:Fault>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    def connection_fault(self) -> str:
        """Generate SOAP fault for connection error."""
        return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
    <SOAP-ENV:Body>
        <SOAP-ENV:Fault>
            <SOAP-ENV:Code>
                <SOAP-ENV:Value>SOAP-ENV:Receiver</SOAP-ENV:Value>
            </SOAP-ENV:Code>
            <SOAP-ENV:Reason>
                <SOAP-ENV:Text xml:lang="en">Connection error to camera</SOAP-ENV:Text>
            </SOAP-ENV:Reason>
        </SOAP-ENV:Fault>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    def generic_fault(self, message: str) -> str:
        """Generate generic SOAP fault."""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
    <SOAP-ENV:Body>
        <SOAP-ENV:Fault>
            <SOAP-ENV:Code>
                <SOAP-ENV:Value>SOAP-ENV:Receiver</SOAP-ENV:Value>
            </SOAP-ENV:Code>
            <SOAP-ENV:Reason>
                <SOAP-ENV:Text xml:lang="en">{message}</SOAP-ENV:Text>
            </SOAP-ENV:Reason>
        </SOAP-ENV:Fault>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""
