import re
import logging
from typing import Tuple
from lxml import etree
from helpers import ONVIFHelpers

logger = logging.getLogger(__name__)

class ONVIFResponseModifier:
    def modify_onvif_response(camera_config, operation, root: etree.Element) -> bytes:
        logger.debug(f"[{camera_config['name']}] Modifying response for operation: {operation}")

        ns = {
                'SOAP-ENV': "http://www.w3.org/2003/05/soap-envelope",
                'tptz': 'http://www.onvif.org/ver20/ptz/wsdl',
                'tt': 'http://www.onvif.org/ver10/schema',
            }

        #Start adding in modifications here
        #Look for another setting in the configuration to fix the flipped X axis
        #instead of using a negative X modifier.

        if operation == 'GetCapabilities':
            return etree.tostring(root)
        elif operation == 'GetProfiles':
            return etree.tostring(root)
        elif operation == 'GetConfiguration':
            logger.warning(f"[{camera_config['name']}]: Post GetConfiguration modification not implemented yet")

            return etree.tostring(root)
        elif operation == 'GetConfigurationOptions':
            spaces = root.find('.//tt:Spaces', namespaces=ns)
            if spaces is None:
                logger.warning(f"[{camera_config['name']}]: Could not find Spaces element in GetConfigurationOptions response")
                return etree.tostring(root)

            # Find RelativePanTiltTranslationSpace
            rel_pt_space = spaces.find('tt:RelativePanTiltTranslationSpace', namespaces=ns)
            if rel_pt_space is None:
                logger.warning(f"[{camera_config['name']}]: Could not find RelativePanTiltTranslationSpace")
                return etree.tostring(root)

            # Check if FOV space already exists
            for space in spaces.findall('tt:RelativePanTiltTranslationSpace', namespaces=ns):
                uri = space.find('{http://www.onvif.org/ver10/schema}URI')
                if uri is not None and 'TranslationSpaceFov' in uri.text:
                    logger.info(f"[{camera_config['name']}]: FOV space already exists, skipping")
                    return etree.tostring(root)

            # Create new FOV space element (copy existing GenericSpace and modify)
            fov_space = etree.Element('{http://www.onvif.org/ver10/schema}RelativePanTiltTranslationSpace')

            uri_elem = etree.SubElement(fov_space, '{http://www.onvif.org/ver10/schema}URI')
            uri_elem.text = 'http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationSpaceFov'

            xrange = etree.SubElement(fov_space, '{http://www.onvif.org/ver10/schema}XRange')
            xmin = etree.SubElement(xrange, '{http://www.onvif.org/ver10/schema}Min')
            xmin.text = '-1'
            xmax = etree.SubElement(xrange, '{http://www.onvif.org/ver10/schema}Max')
            xmax.text = '1'

            yrange = etree.SubElement(fov_space, '{http://www.onvif.org/ver10/schema}YRange')
            ymin = etree.SubElement(yrange, '{http://www.onvif.org/ver10/schema}Min')
            ymin.text = '-1'
            ymax = etree.SubElement(yrange, '{http://www.onvif.org/ver10/schema}Max')
            ymax.text = '1'

            # Insert the new FOV space element
            # Find the index after the last RelativePanTiltTranslationSpace
            last_rel_pt_idx = None
            for idx, child in enumerate(spaces):
                if child.tag == '{http://www.onvif.org/ver10/schema}RelativePanTiltTranslationSpace':
                    last_rel_pt_idx = idx

            if last_rel_pt_idx is not None:
                spaces.insert(last_rel_pt_idx + 1, fov_space)
            else:
                spaces.append(fov_space)

            # Convert back to string
            modified_response = etree.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')
            logger.info(f"[{camera_config['name']}]: Successfully added FOV space to GetConfigurationOptions")

            return modified_response
        elif operation == 'GetStatus':
            last_status = camera_config.get('status', 'IDLE')

            PTZStatus = root.find('.//tptz:PTZStatus', namespaces=ns)
            if PTZStatus is None:
                logger.warning(f"[{camera_config['name']}]: Could not find PTZStatus element in GetStatus response")
                return etree.tostring(root)
            
            MoveStatus = PTZStatus.find('.//tt:MoveStatus/tt:PanTilt', namespaces=ns)
            if MoveStatus is None:
                logger.warning(f"[{camera_config['name']}]: Could not find MoveStatus element in GetStatus response")
                return etree.tostring(root)

            logger.debug(f"[{camera_config['name']}]: MoveStatus original: " + MoveStatus.text)
            MoveStatus.text = last_status

            Position = PTZStatus.find('.//tt:Position/tt:PanTilt', namespaces=ns)
            if Position is None:
                logger.warning(f"[{camera_config['name']}]: Could not find Position element in GetStatus response")
                return etree.tostring(root)

            current_x = Position.get('x')
            current_y = Position.get('y')

            logger.debug(f"[{camera_config['name']}]: Current position x={current_x}, y={current_y}")

            last_x = camera_config.get('status_x', None)
            last_y = camera_config.get('status_y', None)

            if last_x is not None and last_y is not None:
                if current_x == last_x and current_y == last_y:
                    MoveStatus.text = "IDLE"
                    ONVIFHelpers.set_idle(camera_config)

            logger.info(f"[{camera_config['name']}]: Moved status set to: " + MoveStatus.text)

            camera_config['status_x'] = current_x
            camera_config['status_y'] = current_y

            return etree.tostring(root)
        elif operation == 'GetPresets':
            return etree.tostring(root)
        elif operation == 'GetServiceCapabilities':

            Capabilities = root.find('.//tptz:Capabilities', namespaces=ns)
            if Capabilities is None:
                logger.warning(f"[{camera_config['name']}]: Could not find Capabilities element in GetServiceCapabilities response")
                return etree.tostring(root)
            
            # Modify Capabilities to add MoveStatus and StatusPosition support
            Capabilities.set('MoveStatus', 'true')
            Capabilities.set('StatusPosition', 'true')

            logger.info(f"[{camera_config['name']}]: Modified Capabilities to include MoveStatus and StatusPosition support")


            return etree.tostring(root)
        elif operation == 'RelativeMove':
            Fault = root.find('.//SOAP-ENV:Fault', namespaces=ns)
            if Fault is None:
                return etree.tostring(root)
            else:
                logger.info(f"[{camera_config['name']}]: Fixing RelativeMoveResponse fault to success")
                Body = root.find('.//SOAP-ENV:Body', namespaces=ns)
                if Body is None:
                    logger.error(f"[{camera_config['name']}]: Could not find Body element to fix RelativeMoveResponse")
                    return etree.tostring(root)
                
                RelativeMoveResponse = etree.Element('{http://www.onvif.org/ver20/ptz/wsdl}RelativeMoveResponse')
                Body.remove(Fault)
                Body.append(RelativeMoveResponse)

            return etree.tostring(root)
        elif operation == 'GoToPreset':
            return etree.tostring(root)
        elif operation == 'ContinuousMove':
            return etree.tostring(root)
        elif operation == 'AbsoluteMove':
            return etree.tostring(root)
        elif operation == 'Stop':
            return etree.tostring(root)
        # Not a tracked operation
        return etree.tostring(root)

    def rewrite_host_urls(camera_config, response_text):
        CAMERA_HOST = camera_config['camera_host']
        CAMERA_PORT = camera_config['camera_port']
        PROXY_HOST = camera_config.get('proxy_host', '127.0.0.1')
        PROXY_PORT = camera_config['proxy_port']

        old_url = f"http://{CAMERA_HOST}:{CAMERA_PORT}/onvif/service"
        new_url = f"http://{PROXY_HOST}:{PROXY_PORT}/onvif/service"
        response_str = response_text.replace(old_url, new_url)
        return response_str

    def fix_header_size(camera_config, operation, response_text):
        CAMERA_NAME = camera_config['name']
        CAMERA_HOST = camera_config['camera_host']
        CAMERA_PORT = camera_config['camera_port']
        PROXY_HOST = camera_config.get('proxy_host', '127.0.0.1')
        PROXY_PORT = camera_config['proxy_port']
    
        old_url = f"http://{CAMERA_HOST}:{CAMERA_PORT}/onvif/service"
        new_url = f"http://{PROXY_HOST}:{PROXY_PORT}/onvif/service"
        response_str = response_text.replace(old_url, new_url)
        parts = response_str.split('\r\n\r\n', 1)
        if len(parts) == 2:
            headers = parts[0]
            body = parts[1]
            new_content_length = len(body.encode('utf-8'))
            new_headers = re.sub(r'Content-Length: [0-9]+', f'Content-Length: {new_content_length}', headers)
            response_str = new_headers + '\r\n\r\n' + body
        return response_str