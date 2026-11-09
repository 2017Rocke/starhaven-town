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
        self.supports_persistence = sys.platform == "darwin"
        self._keychain = None

    @property
    def storage(self):
        return "keychain" if self.supports_persistence else "environment"

    def _account(self, base_url):
        # Changing the destination must never silently reuse another server's key.
        return hashlib.sha256((self.namespace + "\n" + base_url).encode()).hexdigest()

    def _native(self):
        if self._keychain is None:
            self._keychain = _MacKeychain()
        return self._keychain

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
        result = ctypes.c_void_p()
        with self.dictionary(account, {"kSecReturnData": self.constant("kCFBooleanTrue"),
                                       "kSecMatchLimit": self.constant("kSecMatchLimitOne")}) as query:
            status = self.sec.SecItemCopyMatching(query, ctypes.byref(result))
        if status == self.NOT_FOUND:
            return ""
        if status != 0 or not result.value:
            raise CredentialError("无法读取钥匙串中的密钥，请解锁钥匙串或重新保存密钥。")
        try:
            length = self.cf.CFDataGetLength(result)
            if not 0 <= length <= 8192:
                raise CredentialError("钥匙串中的密钥格式无效，请重新保存。")
            return ctypes.string_at(self.cf.CFDataGetBytePtr(result), length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialError("钥匙串中的密钥格式无效，请重新保存。") from exc
        finally:
            self.cf.CFRelease(result)

    def set(self, account, value):
        with self.dictionary(account) as query:
            if not value:
                status = self.sec.SecItemDelete(query)
                if status not in (0, self.NOT_FOUND):
                    raise CredentialError("无法删除钥匙串中的密钥，请检查钥匙串权限。")
                return
            with self.dictionary(values={"kSecValueData": value.encode("utf-8")}) as attributes:
                status = self.sec.SecItemUpdate(query, attributes)
            if status == self.NOT_FOUND:
                with self.dictionary(account, {"kSecValueData": value.encode("utf-8")}) as record:
                    status = self.sec.SecItemAdd(record, None)
            if status != 0:
                raise CredentialError("无法保存到 macOS 钥匙串，请允许小镇访问钥匙串后重试。")
