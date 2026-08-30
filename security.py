"""
security.py — Vérification de la signature HMAC des webhooks GitHub.

Correspond à la méthode verifierSignature(secret): Boolean de la classe
WebhookServer dans le diagramme de classes.
"""

import hashlib
import hmac


def verify_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """
    Vérifie que la signature X-Hub-Signature-256 reçue correspond bien
    au HMAC SHA-256 calculé sur le corps brut de la requête.

    On utilise hmac.compare_digest pour une comparaison en temps constant,
    ce qui protège contre les attaques par timing (timing attacks).

    :param payload_body:      Corps brut (bytes) de la requête entrante.
    :param signature_header:  Valeur du header X-Hub-Signature-256 (format "sha256=<hex>").
    :param secret:            GITHUB_WEBHOOK_SECRET chargé depuis .env.
    :return:                  True si la signature est valide, False sinon.
    """
    if not signature_header:
        return False

    mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256,
    )
    expected_signature = f"sha256={mac.hexdigest()}"

    return hmac.compare_digest(expected_signature, signature_header)
