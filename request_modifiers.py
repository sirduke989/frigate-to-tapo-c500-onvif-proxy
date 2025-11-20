import re
import logging
from typing import Tuple
from lxml import etree
from helpers import ONVIFHelpers

logger = logging.getLogger(__name__)

class ONVIFRequestModifier:
    def modify_onvif_request(camera_config, operation, root: etree.Element) -> str:
        logger.debug(f"[{camera_config['name']}] Modifying request for operation: {operation}")
        RELATIVE_MOVE_X_MULTIPLIER = camera_config.get('x_multiplier', 1.0)
        RELATIVE_MOVE_Y_MULTIPLIER = camera_config.get('y_multiplier', 1.0)

        if operation == 'GetCapabilities':
            return etree.tostring(root)
        elif operation == 'GetProfiles':
            return etree.tostring(root)
        elif operation == 'GetConfigurationOptions':
            return etree.tostring(root)
        elif operation == 'GetStatus':
            return etree.tostring(root)
        elif operation == 'GetPresets':
            return etree.tostring(root)
        elif operation == 'GetServiceCapabilities':
            return etree.tostring(root)
        elif operation == 'RelativeMove':
            logger.debug(f"[{camera_config['name']}] Processing pre_RelativeMove_request")

            pan_tilt = root.find('.//{http://www.onvif.org/ver10/schema}PanTilt')
            if not pan_tilt is None:
                x_orig = pan_tilt.get('x')
                y_orig = pan_tilt.get('y')
                if x_orig is not None and y_orig is not None:
                    x_orig_f = float(x_orig)
                    y_orig_f = float(y_orig)
                    new_x = x_orig_f * RELATIVE_MOVE_X_MULTIPLIER
                    new_y = y_orig_f * RELATIVE_MOVE_Y_MULTIPLIER
                    new_x = max(-1.0, min(1.0, new_x))
                    new_y = max(-1.0, min(1.0, new_y))
                    pan_tilt.set('x', f"{new_x:g}")
                    pan_tilt.set('y', f"{new_y:g}")
                    logger.info(f"[{camera_config['name']}] Changed x={x_orig} to {new_x:g} and y={y_orig} to {new_y:g}")

                    ONVIFHelpers.set_moving(camera_config)

            return etree.tostring(root)
        elif operation == 'GoToPreset':
            ONVIFHelpers.set_moving(camera_config)

            return etree.tostring(root)
        elif operation == 'ContinuousMove':
            ONVIFHelpers.set_moving(camera_config)

            return etree.tostring(root)
        elif operation == 'AbsoluteMove':
            ONVIFHelpers.set_moving(camera_config)
            
            return etree.tostring(root)
        elif operation == 'Stop':
            # Cancel move timer and set status to IDLE immediately
            ONVIFHelpers.set_idle(camera_config)

            return etree.tostring(root)
        # Not a tracked operation
        return etree.tostring(root)