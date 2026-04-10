import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict

from .settings import SECRET_KEY, TOKEN_TTL_HOURS


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 120000).hex()
    return f'{salt}${digest}'


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split('$', 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), f'{salt}${digest}')


def create_token(payload: Dict[str, str]) -> str:
    body = dict(payload)
    body['exp'] = int((datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)).timestamp())
    raw = json.dumps(body, separators=(',', ':')).encode('utf-8')
    encoded = base64.urlsafe_b64encode(raw).rstrip(b'=')
    signature = hmac.new(SECRET_KEY.encode('utf-8'), encoded, hashlib.sha256).digest()
    encoded_sig = base64.urlsafe_b64encode(signature).rstrip(b'=')
    return f'{encoded.decode()}.{encoded_sig.decode()}'


def decode_token(token: str) -> Dict[str, str]:
    encoded, encoded_sig = token.split('.', 1)
    expected_sig = base64.urlsafe_b64encode(
        hmac.new(SECRET_KEY.encode('utf-8'), encoded.encode('utf-8'), hashlib.sha256).digest()
    ).rstrip(b'=')
    if not hmac.compare_digest(expected_sig, encoded_sig.encode('utf-8')):
        raise PermissionError('Invalid token signature')
    padded = encoded + '=' * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode('utf-8')))
    if int(payload.get('exp', 0)) < int(datetime.now(timezone.utc).timestamp()):
        raise PermissionError('Token expired')
    return payload
