"""Cross-platform storage for the app's shared SSH login.

Windows uses Credential Manager (the historical behavior). Elsewhere the
secret goes to the desktop keyring when `keyring` is installed and a backend is
available, and otherwise to a 0600 JSON file under the user's config directory.
"""

import ctypes
import json
import os
import stat
from pathlib import Path

SERVICE_NAME = "ClassroomRoster/Ansible"
MAX_PASSWORD_BYTES = 2560

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168

_WINDOWS_TYPES = None


def _windows_types():
    """Build the Win32 credential structs lazily: they need Windows-only ctypes types."""
    global _WINDOWS_TYPES
    if _WINDOWS_TYPES is None:
        from ctypes import wintypes

        class _FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class _CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", _FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        _WINDOWS_TYPES = (wintypes, _FILETIME, _CREDENTIAL)
    return _WINDOWS_TYPES


def validate_username_password(username, password):
    """Shared validation so every backend rejects the same input."""
    username = str(username).strip()
    password = str(password)
    if username.lower() != "root":
        raise ValueError("The classroom SSH account must be root.")
    if not password:
        raise ValueError("Enter the shared root password.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES // 2:
        raise ValueError("Enter a non-empty password no longer than 1,280 characters.")
    return username, password


class WindowsCredentialStore:
    """Store the app's shared SSH login in the current Windows user's Credential Manager."""

    location = "Windows Credential Manager"

    def __init__(self, target=SERVICE_NAME):
        if os.name != "nt":
            raise RuntimeError("Windows Credential Manager is only available on Windows.")
        wintypes, _FILETIME, _CREDENTIAL = _windows_types()
        self.target = target
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                          ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi.CredFree.restype = None

    def save(self, username, password):
        username, password = validate_username_password(username, password)
        secret = password.encode("utf-16-le")
        if len(secret) > MAX_PASSWORD_BYTES:
            raise ValueError("Enter a non-empty password no longer than 1,280 characters.")
        blob = ctypes.create_string_buffer(secret, len(secret))
        credential = _CREDENTIAL()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = self.target
        credential.CredentialBlobSize = len(secret)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = username
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def load(self):
        pointer = ctypes.POINTER(_CREDENTIAL)()
        if not self._advapi.CredReadW(self.target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return None
            raise ctypes.WinError(error)
        try:
            credential = pointer.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return {"username": credential.UserName or "root", "password": raw.decode("utf-16-le")}
        finally:
            self._advapi.CredFree(pointer)


class KeyringCredentialStore:
    """Store the shared SSH login in the desktop keyring (GNOME Keyring, KWallet, ...)."""

    location = "the system keyring"

    def __init__(self, target=SERVICE_NAME, module=None):
        import keyring as module
        self.target = target
        self._keyring = module

    @staticmethod
    def available(target=SERVICE_NAME):
        try:
            import keyring
            keyring.get_keyring()
        except Exception:
            return False
        return True

    def save(self, username, password):
        username, password = validate_username_password(username, password)
        self._keyring.set_password(target=self.target, username=username, password=password)

    def load(self):
        username = "root"
        password = self._keyring.get_password(self.target, username)
        if password is None:
            return None
        return {"username": username, "password": password}


class FileCredentialStore:
    """Fallback for Linux/macOS hosts with no keyring: a 0600 JSON file in the config dir."""

    def __init__(self, target=SERVICE_NAME, path=None):
        self.target = target
        if path is None:
            base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            path = base / "classroom-roster" / "credentials.json"
        self.path = Path(path)
        self.location = f"{self.path} (owner-only file)"

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read the saved credentials file: {error}") from error
        password = data.get("password")
        if not password:
            return None
        return {"username": data.get("username") or "root", "password": password}

    def save(self, username, password):
        username, password = validate_username_password(username, password)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(stat.S_IRWXU)
        except OSError:
            pass
        # Create with 0600 from the start so the secret is never briefly world-readable.
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"username": username, "password": password}, handle)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def load(self):
        return self._read()


def get_credential_store():
    """Best available credential backend for this operating system."""
    if os.name == "nt":
        return WindowsCredentialStore()
    if KeyringCredentialStore.available():
        try:
            return KeyringCredentialStore()
        except Exception:
            # A broken or unusable keyring must not stop the app from starting.
            pass
    return FileCredentialStore()


def default_credential_store():
    """Alias kept for callers that read better with the `default_` prefix."""
    return get_credential_store()