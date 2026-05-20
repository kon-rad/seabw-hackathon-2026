"""
Browser Push Notification Service
Manages VAPID keys, push subscriptions, and sends Web Push notifications
when simulations complete. Uses the pywebpush library (optional dependency).
"""

import fcntl
import os
import json
import base64
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List

from ..utils.logger import get_logger

logger = get_logger('miroshark.push_notification')

_UPLOADS_DIR = os.path.join(os.path.dirname(__file__), '../../uploads')
# SECURITY: contains the VAPID private key in plaintext. Do not expose the
# uploads/ directory publicly (e.g. via a static file server or open bucket).
VAPID_KEYS_PATH = os.path.join(_UPLOADS_DIR, 'vapid_keys.json')
SUBSCRIPTIONS_DIR = os.path.join(_UPLOADS_DIR, 'push_subscriptions')

# Cache keys in memory once loaded
_vapid_keys_cache: Optional[Dict[str, str]] = None
_cache_lock = threading.Lock()


def _get_or_create_vapid_keys() -> Dict[str, str]:
    """Load VAPID keys from disk, generating them on first use.

    Returns a dict with 'private_key' (PEM string) and 'public_key'
    (URL-safe base64 uncompressed EC point, no padding) — or an empty
    dict if pywebpush is not installed.
    """
    global _vapid_keys_cache

    with _cache_lock:
        if _vapid_keys_cache is not None:
            return _vapid_keys_cache

        os.makedirs(_UPLOADS_DIR, exist_ok=True)

        if os.path.exists(VAPID_KEYS_PATH):
            try:
                with open(VAPID_KEYS_PATH, 'r') as f:
                    _vapid_keys_cache = json.load(f)
                return _vapid_keys_cache
            except Exception as exc:
                logger.warning(f"Failed to read VAPID keys, regenerating: {exc}")

        # Generate new keys
        try:
            from py_vapid import Vapid
            from cryptography.hazmat.primitives.serialization import (
                Encoding,
                PublicFormat,
                PrivateFormat,
                NoEncryption,
            )

            vapid = Vapid()
            vapid.generate_keys()

            private_key_pem = vapid.private_key.private_bytes(
                encoding=Encoding.PEM,
                format=PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=NoEncryption(),
            ).decode()

            public_key_raw = vapid.public_key.public_bytes(
                encoding=Encoding.X962,
                format=PublicFormat.UncompressedPoint,
            )
            public_key_b64url = base64.urlsafe_b64encode(public_key_raw).rstrip(b'=').decode()

            keys = {
                'private_key': private_key_pem,
                'public_key': public_key_b64url,
            }

            with open(VAPID_KEYS_PATH, 'w') as f:
                json.dump(keys, f, indent=2)

            _vapid_keys_cache = keys
            logger.info("Generated new VAPID keys for browser push notifications")
            return keys

        except ImportError:
            logger.warning(
                "pywebpush is not installed — browser push notifications are disabled. "
                "Run: pip install pywebpush"
            )
            _vapid_keys_cache = {}
            return {}
        except Exception as exc:
            logger.error(f"Failed to generate VAPID keys: {exc}")
            _vapid_keys_cache = {}
            return {}


def get_vapid_public_key() -> Optional[str]:
    """Return the VAPID public key suitable for the frontend applicationServerKey."""
    return _get_or_create_vapid_keys().get('public_key')


def save_subscription(simulation_id: str, subscription: Dict[str, Any]) -> None:
    """Persist a push subscription for a simulation.

    Deduplicates by endpoint so re-subscribing from the same browser
    does not create duplicate entries.
    """
    os.makedirs(SUBSCRIPTIONS_DIR, exist_ok=True)
    path = os.path.join(SUBSCRIPTIONS_DIR, f'{simulation_id}.json')

    lock_path = path + '.lock'
    with open(lock_path, 'w') as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            subscriptions: List[Dict[str, Any]] = []
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        subscriptions = json.load(f)
                except Exception:
                    subscriptions = []

            # Remove any existing entry for this endpoint (dedup / refresh)
            endpoint = subscription.get('endpoint', '')
            subscriptions = [s for s in subscriptions if s.get('endpoint') != endpoint]
            subscriptions.append({**subscription, 'saved_at': datetime.now().isoformat()})

            with open(path, 'w') as f:
                json.dump(subscriptions, f, indent=2)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

    logger.info(f"Stored push subscription for simulation {simulation_id}")


def send_push_notification(
    simulation_id: str,
    title: str,
    body: str,
    url: str = '/',
) -> None:
    """Fire push notifications to all subscribers of a simulation.

    Runs in a background daemon thread to avoid blocking the simulation
    monitor. Expired/invalid subscriptions (HTTP 404/410) are pruned.
    """

    def _send() -> None:
        try:
            from pywebpush import webpush
        except ImportError:
            logger.warning("pywebpush not installed — skipping push notification")
            return

        keys = _get_or_create_vapid_keys()
        if not keys:
            return

        path = os.path.join(SUBSCRIPTIONS_DIR, f'{simulation_id}.json')
        if not os.path.exists(path):
            return

        try:
            with open(path, 'r') as f:
                subscriptions: List[Dict[str, Any]] = json.load(f)
        except Exception as exc:
            logger.error(f"Failed to load subscriptions for {simulation_id}: {exc}")
            return

        if not subscriptions:
            return

        payload = json.dumps({
            'title': title,
            'body': body,
            'url': url,
            'simulation_id': simulation_id,
        })

        stale_endpoints = []

        for sub in subscriptions:
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=keys['private_key'],
                    vapid_claims={'sub': 'mailto:noreply@miroshark.app'},
                )
                logger.info(f"Push notification sent for simulation {simulation_id}")
            except Exception as exc:
                # WebPushException is the specific type but we catch broadly for safety
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status in (404, 410):
                    # Subscription expired or unsubscribed — prune it
                    stale_endpoints.append(sub.get('endpoint'))
                    logger.info(
                        f"Pruning stale push subscription for {simulation_id}: {status}"
                    )
                else:
                    logger.warning(f"Push notification failed for {simulation_id}: {exc}")

        # Prune stale subscriptions
        if stale_endpoints:
            subscriptions = [
                s for s in subscriptions if s.get('endpoint') not in stale_endpoints
            ]
            try:
                with open(path, 'w') as f:
                    json.dump(subscriptions, f, indent=2)
            except Exception as exc:
                logger.error(f"Failed to prune stale subscriptions: {exc}")

    thread = threading.Thread(target=_send, daemon=True, name=f'push-{simulation_id}')
    thread.start()
