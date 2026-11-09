"""OS credentials, never a plaintext key file or a command-line argument."""
import ctypes
import hashlib
import os
import sys
from contextlib import contextmanager


class CredentialError(RuntimeError):
    pass


class CredentialStore:
    def __init__(self, data_dir):
        self.namespace = str(data_dir)
        self.supports_persistence = sys.platform in ("darwin", "win32")
        self._backend = None

    @property
    def storage(self):
        if sys.platform == "darwin":
            return "keychain"
        if sys.platform == "win32":
            return "credential-manager"
        return "environment"

    def _account(self, base_url):
        # Changing the destination must never silently reuse another server's key.
        return hashlib.sha256((self.namespace + "\n" + base_url).encode()).hexdigest()

    def _native(self):
        if self._backend is None:
            if sys.platform == "darwin":
                self._backend = _MacKeychain()
            elif sys.platform == "win32":
                self._backend = _WindowsCredentialManager()
            else:
                raise CredentialError("当前系统不支持持久化密钥存储。")
        return self._backend

    def get(self, base_url):
        environment = os.environ.get("STARHAVEN_API_KEY", "").strip()
        if environment:
            return environment
        if not self.supports_persistence or not base_url:
            return ""
        return self._native().get(self._account(base_url))

    def set(self, base_url, value):
        if not self.supports_persistence:
            if value:
                raise CredentialError("此系统暂不保存密钥。请通过 STARHAVEN_API_KEY 环境变量配置；本地无密钥模型可留空。")
            return
        self._native().set(self._account(base_url), value)


class _WindowsCredentialManager:
    """Windows Credential Manager generic credentials via advapi32."""

    SERVICE = "StarhavenTown/model-api"
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self):
        try:
            self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        except OSError as exc:
            raise CredentialError("无法打开 Windows 凭据管理器。") from exc

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", ctypes.c_uint32),
                ("Type", ctypes.c_uint32),
                ("TargetName", ctypes.c_wchar_p),
                ("Comment", ctypes.c_wchar_p),
                ("LastWritten", ctypes.c_uint64),
                ("CredentialBlobSize", ctypes.c_uint32),
                ("CredentialBlob", ctypes.c_void_p),
                ("Persist", ctypes.c_uint32),
                ("AttributeCount", ctypes.c_uint32),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.c_wchar_p),
                ("UserName", ctypes.c_wchar_p),
            ]

        self.CREDENTIAL = CREDENTIAL
        self.advapi.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        self.advapi.CredReadW.restype = ctypes.c_int
        self.advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), ctypes.c_uint32]
        self.advapi.CredWriteW.restype = ctypes.c_int
        self.advapi.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        self.advapi.CredDeleteW.restype = ctypes.c_int
        self.advapi.CredFree.argtypes = [ctypes.c_void_p]
        self.advapi.CredFree.restype = None

    def _target(self, account):
        return self.SERVICE + "/" + account

    def get(self, account):
        pointer = ctypes.POINTER(self.CREDENTIAL)()
        if not self.advapi.CredReadW(self._target(account), self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == self.ERROR_NOT_FOUND:
                return ""
            raise CredentialError("无法读取 Windows 凭据，请检查系统权限后重试。")
        try:
            credential = pointer.contents
            size = credential.CredentialBlobSize
            if not size or not credential.CredentialBlob:
                return ""
            raw = ctypes.string_at(credential.CredentialBlob, size)
            try:
                return raw.decode("utf-16-le").rstrip("\x00")
            except UnicodeError as exc:
                raise CredentialError("凭据中的密钥格式无效，请重新保存。") from exc
        finally:
            self.advapi.CredFree(pointer)

    def set(self, account, value):
        target = self._target(account)
        if not value:
            if not self.advapi.CredDeleteW(target, self.CRED_TYPE_GENERIC, 0):
                error = ctypes.get_last_error()
                if error not in (0, self.ERROR_NOT_FOUND):
                    raise CredentialError("无法删除 Windows 凭据，请检查系统权限后重试。")
            return
        blob = value.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(blob)
        credential = self.CREDENTIAL()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.c_void_p)
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "StarhavenTown"
        if not self.advapi.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialError("无法保存到 Windows 凭据管理器，请检查系统权限后重试。")


class _MacKeychain:
    """Small Security.framework binding; all owned Core Foundation values released."""
    SERVICE = "com.starhaven.town.model-api"
    NOT_FOUND = -25300
    DUPLICATE = -25299

    def __init__(self):
        try:
            self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            self.sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
            void = ctypes.c_void_p
            self.cf.CFStringCreateWithCString.argtypes = [void, ctypes.c_char_p, ctypes.c_uint32]
            self.cf.CFStringCreateWithCString.restype = void
            self.cf.CFDictionaryCreateMutable.argtypes = [void, ctypes.c_long, void, void]
            self.cf.CFDictionaryCreateMutable.restype = void
            self.cf.CFDictionarySetValue.argtypes = [void, void, void]
            self.cf.CFDataCreate.argtypes = [void, void, ctypes.c_long]
            self.cf.CFDataCreate.restype = void
            self.cf.CFDataGetLength.argtypes = [void]
            self.cf.CFDataGetLength.restype = ctypes.c_long
            self.cf.CFDataGetBytePtr.argtypes = [void]
            self.cf.CFDataGetBytePtr.restype = void
            self.cf.CFRelease.argtypes = [void]
            self.sec.SecItemCopyMatching.argtypes = [void, ctypes.POINTER(void)]
            self.sec.SecItemAdd.argtypes = [void, ctypes.POINTER(void)]
            self.sec.SecItemUpdate.argtypes = [void, void]
            self.sec.SecItemDelete.argtypes = [void]
            for name in ("SecItemCopyMatching", "SecItemAdd", "SecItemUpdate", "SecItemDelete"):
                getattr(self.sec, name).restype = ctypes.c_int32
        except (OSError, ValueError, AttributeError) as exc:
            raise CredentialError("无法打开 macOS 钥匙串，请检查系统访问权限。") from exc

    def constant(self, name):
        framework = self.cf if name.startswith("kCF") else self.sec
        return ctypes.c_void_p.in_dll(framework, name).value

    @contextmanager
    def dictionary(self, account=None, values=None):
        key_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks"))
        value_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks"))
        result = self.cf.CFDictionaryCreateMutable(None, 0, key_callbacks, value_callbacks)
        if not result:
            raise CredentialError("暂时无法访问钥匙串。")
        owned = []
        try:
            if account is not None:
                self.cf.CFDictionarySetValue(result, self.constant("kSecClass"), self.constant("kSecClassGenericPassword"))
                for name, text in (("kSecAttrService", self.SERVICE), ("kSecAttrAccount", account)):
                    value = self.cf.CFStringCreateWithCString(None, text.encode("utf-8"), 0x08000100)
                    owned.append(value)
                    self.cf.CFDictionarySetValue(result, self.constant(name), value)
            for name, value in (values or {}).items():
                if isinstance(value, bytes):
                    buffer = ctypes.create_string_buffer(value)
                    value = self.cf.CFDataCreate(None, buffer, len(value))
                    owned.append(value)
                self.cf.CFDictionarySetValue(result, self.constant(name), value)
            yield result
        finally:
            self.cf.CFRelease(result)
            for value in owned:
                if value:
                    self.cf.CFRelease(value)

    def get(self, account):
        with self.dictionary(account, {"kSecReturnData": self.constant("kCFBooleanTrue"), "kSecMatchLimit": self.constant("kSecMatchLimitOne")}) as query:
            data = ctypes.c_void_p()
            status = self.sec.SecItemCopyMatching(query, ctypes.byref(data))
            if status == self.NOT_FOUND:
                return ""
            if status != 0 or not data:
                raise CredentialError("无法读取钥匙串中的密钥，请解锁钥匙串或重新保存密钥。")
            try:
                length = self.cf.CFDataGetLength(data)
                pointer = self.cf.CFDataGetBytePtr(data)
                if not pointer or length < 0:
                    raise CredentialError("钥匙串中的密钥格式无效，请重新保存。")
                return ctypes.string_at(pointer, length).decode("utf-8")
            except UnicodeError as exc:
                raise CredentialError("钥匙串中的密钥格式无效，请重新保存。") from exc
            finally:
                self.cf.CFRelease(data)

    def set(self, account, value):
        if not value:
            with self.dictionary(account) as query:
                status = self.sec.SecItemDelete(query)
                if status not in (0, self.NOT_FOUND):
                    raise CredentialError("无法删除钥匙串中的密钥，请检查钥匙串权限。")
            return
        payload = value.encode("utf-8")
        with self.dictionary(account, {"kSecValueData": payload, "kSecAttrAccessible": self.constant("kSecAttrAccessibleWhenUnlockedThisDeviceOnly")}) as add:
            status = self.sec.SecItemAdd(add, None)
            if status == 0:
                return
            if status != self.DUPLICATE:
                raise CredentialError("无法保存到 macOS 钥匙串，请允许小镇访问钥匙串后重试。")
        with self.dictionary(account) as query, self.dictionary(values={"kSecValueData": payload}) as update:
            status = self.sec.SecItemUpdate(query, update)
            if status != 0:
                raise CredentialError("无法更新 macOS 钥匙串中的密钥，请允许小镇访问钥匙串后重试。")
