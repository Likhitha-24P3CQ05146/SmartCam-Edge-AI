"""Run once to create VAPID keys. Keep the PRIVATE key secret."""
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
key = ec.generate_private_key(ec.SECP256R1())
private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
public_point = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
public_b64 = base64.urlsafe_b64encode(public_point).decode().rstrip('=')
print('SMARTCAM_VAPID_PRIVATE_KEY (store in backend/vapid_private.pem):')
print(private_pem)
print('SMARTCAM_VAPID_PUBLIC_KEY:', public_b64)
