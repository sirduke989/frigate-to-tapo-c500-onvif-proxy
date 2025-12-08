import re
import logging
import threading
from typing import Tuple
from lxml import etree

logger = logging.getLogger(__name__)

class ONVIFHelpers:
    @staticmethod
    def set_moving(camera_config: dict):
        """
        Set camera status to 'MOVING' and schedule a timer to set it back to 'IDLE'
        after `timeout` seconds. Cancels any existing timer first.
        Stores the timer object in camera_config['_move_timer'].
        """
        timeout = camera_config.get('move_timeout', 10)
        # Cancel existing timer if present
        existing = camera_config.get('_move_timer')
        if isinstance(existing, threading.Timer):
            try:
                existing.cancel()
            except Exception:
                pass
            camera_config.pop('_move_timer', None)

        camera_config['status'] = 'MOVING'
        logger.info(f"[{camera_config['name']}] Set status: to {camera_config.get('status')}")

        def _set_idle():
            ONVIFHelpers.set_idle(camera_config)
            logger.warning(f"[{camera_config.get('name')}]: Status timeout expired; set to IDLE")

        timer = threading.Timer(timeout, _set_idle)
        timer.daemon = True
        camera_config['_move_timer'] = timer
        timer.start()
        logger.info("Move timeout started")

    @staticmethod
    def set_idle(camera_config: dict):
        """
        Immediately set camera status to 'IDLE' and cancel any running move timer.
        """
        timer = camera_config.get('_move_timer')
        if isinstance(timer, threading.Timer):
            try:
                timer.cancel()
            except Exception:
                pass
            camera_config.pop('_move_timer', None)
        camera_config['status'] = 'IDLE'
