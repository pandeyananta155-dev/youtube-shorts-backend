from cryptography.fernet import Fernet, InvalidToken


class TokenEncryptionError(RuntimeError):
    pass


class TokenCipher:
    def __init__(self, key: str | None):
        if not key:
            raise TokenEncryptionError("OAUTH_TOKEN_ENCRYPTION_KEY is not configured")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise TokenEncryptionError("OAUTH_TOKEN_ENCRYPTION_KEY is invalid") from exc

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted_token: str) -> str:
        try:
            return self._fernet.decrypt(encrypted_token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
            raise TokenEncryptionError("Stored OAuth credential cannot be decrypted") from exc