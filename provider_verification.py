"""Local, expiring Messages budget probes. No user material or credentials persisted.

This is sampled interoperability evidence, not a provider billing guarantee.
The bridge rechecks every controlled response and native grants remain separate.
"""
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error
import urllib.request

PROFILE = 'local-messages-text-v1'
EXTENDED_PROFILE = 'local-messages-text-v2'
FILENAME = 'model-budget-verifications.json'
TTL = 30 * 86400
OUTPUT_LIMIT = 4096
INPUT_LIMIT = 65536
EXTENDED_OUTPUT_LIMIT = 32768
EXTENDED_INPUT_LIMIT = 262144
_PROFILE_LIMITS = {
    PROFILE: dict(max_output_tokens=4096, max_request_bytes=65536, socket_timeout=120),
    EXTENDED_PROFILE: dict(max_output_tokens=32768, max_request_bytes=262144, socket_timeout=600),
}
MAX_FILE = 262144
_LOCK = threading.RLock()


class VerificationError(ValueError):
    pass


def verification_profile(output_limit=OUTPUT_LIMIT, input_limit=INPUT_LIMIT):
    if type(output_limit) is int and type(input_limit) is int:
        for profile, limits in _PROFILE_LIMITS.items():
            if (output_limit, input_limit) == (limits['max_output_tokens'], limits['max_request_bytes']):
                return profile
    raise VerificationError('验证上限只支持 4096/65536 或 32768/262144 的固定组合')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def identity(config):
    from provider_bridge import endpoint_identity
    if config.get('mode', 'api') != 'api' or config.get('protocol') != 'anthropic':
        raise VerificationError('本机验证当前只支持 Anthropic Messages 文本接口')
    result = endpoint_identity(config)
    if not result['base_url'] or not result['model']:
        raise VerificationError('请先保存完整的模型服务设置')
    return result


def probe_requests(config, *, output_limit=OUTPUT_LIMIT, input_limit=INPUT_LIMIT):
    """Four fixed synthetic cases; upper bound applies to serialized request bytes."""
    verification_profile(output_limit, input_limit)
    endpoint = identity(config)
    def body(prompt, cap):
        return dict(model=endpoint['model'], stream=False, max_tokens=cap,
                    system='你只处理合成验证材料，不访问工具或其他资源。请遵守用户的输出格式。',
                    messages=[dict(role='user', content=prompt)])
    sequence = '从1开始逐行输出一千个整数，每行一个，不要解释，一直输出到1000。'
    yield 'cap16', body(sequence, 16)
    yield 'cap128', body(sequence, 128)
    yield 'json%d' % output_limit, body('这是虚构的软件练习，与任何真实项目无关。需求：用户可以在待办清单添加事项，'
        '标题不得为空，可以勾选完成。请输出一个JSON对象，字段summary字符串、scope字符串数组、'
        'acceptance_criteria字符串数组、questions数组。简短即可，不使用Markdown。', output_limit)
    # Mix multilingual text and code-shaped data without accessing a source repo.
    prefix = '以下是重复的合成材料，仅用于验证输入计量，请忽略内容，只回答OK。\n'
    unit = '材料 alpha 用户 café 😀 value = 42; <sample>测试</sample>\n'
    long_body = body(prefix, 128)
    encoded_unit = json.dumps(unit, ensure_ascii=False)[1:-1].encode('utf-8')
    count = (input_limit-len(json.dumps(long_body, ensure_ascii=False).encode('utf-8'))) // len(encoded_unit)
    long_body['messages'][0]['content'] += unit * count
    remaining = input_limit - len(json.dumps(long_body, ensure_ascii=False).encode('utf-8'))
    long_body['messages'][0]['content'] += 'x' * remaining
    yield 'input%d' % input_limit, long_body


def observation(case, payload, cap, response, status=200, *, output_limit=OUTPUT_LIMIT, input_limit=INPUT_LIMIT):
    """Retain lengths, token counts and stop enums only; never model text."""
    from provider_bridge import _anthropic_usage
    if not isinstance(response, dict):
        raise VerificationError('服务返回的 Messages 结构无效')
    usage = _anthropic_usage(response.get('usage'))
    blocks = response.get('content')
    if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
        raise VerificationError('服务返回的 Messages 内容结构无效')
    strings = [b.get('text') for b in blocks if b.get('type') == 'text']
    if any(not isinstance(v, str) for v in strings):
        raise VerificationError('服务返回的文字块格式无效')
    text = '\n'.join(strings)
    thinking = sum(len(b.get('thinking', '')) for b in blocks if isinstance(b.get('thinking'), str))
    raw_usage = response.get('usage', {})
    details = raw_usage.get('output_tokens_details') if isinstance(raw_usage, dict) else None
    reasoning_tokens = details.get('thinking_tokens') if isinstance(details, dict) else None
    valid_json = False
    if case == 'json%d' % output_limit:
        try:
            decoded = json.loads(text)
            valid_json = (isinstance(decoded, dict) and isinstance(decoded.get('summary'), str)
                and bool(decoded['summary'].strip())
                and all(isinstance(decoded.get(key), list)
                    and all(isinstance(item, str) and bool(item.strip()) for item in decoded[key])
                    for key in ('scope', 'acceptance_criteria', 'questions'))
                and bool(decoded['scope']) and bool(decoded['acceptance_criteria']))
        except ValueError:
            pass
    stop = response.get('stop_reason')
    record = dict(case=case, request_bytes=len(payload), max_tokens=cap, http_status=status,
        stop_reason=stop if stop in ('end_turn', 'stop_sequence', 'max_tokens') else 'other',
        usage=usage, text_chars=len(text), thinking_chars=thinking,
        thinking_tokens=reasoning_tokens if type(reasoning_tokens) is int and reasoning_tokens >= 0 else None,
        json_schema_valid=valid_json, request_sha256=hashlib.sha256(payload).hexdigest(),
        response_sha256=fingerprint(response))
    # Unknown usage or limits fail before a receipt can be issued.
    check_observation(record, output_limit=output_limit, input_limit=input_limit)
    return record


def check_observation(record, *, output_limit=OUTPUT_LIMIT, input_limit=INPUT_LIMIT):
    verification_profile(output_limit, input_limit)
    required = {'case', 'request_bytes', 'max_tokens', 'http_status', 'stop_reason', 'usage',
                'text_chars', 'thinking_chars', 'thinking_tokens', 'json_schema_valid',
                'request_sha256', 'response_sha256'}
    if (not isinstance(record, dict) or set(record) != required
            or type(record['http_status']) is not int or record['http_status'] != 200):
        raise VerificationError('验证回执不完整')
    if record['case'] not in ('cap16', 'cap128', 'json%d' % output_limit, 'input%d' % input_limit):
        raise VerificationError('验证场景不匹配')
    for key in ('request_bytes', 'max_tokens', 'text_chars', 'thinking_chars'):
        if type(record[key]) is not int or not 0 <= record[key] <= 1000000:
            raise VerificationError('验证计数无效')
    if not 1 <= record['request_bytes'] <= input_limit or not 1 <= record['max_tokens'] <= output_limit:
        raise VerificationError('验证请求不在约定范围')
    usage = record['usage']
    if (not isinstance(usage, dict) or set(usage) != {'prompt_tokens', 'completion_tokens', 'total_tokens'}
            or any(type(n) is not int or n < 0 for n in usage.values())
            or usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens']):
        raise VerificationError('模型没有返回可核验的完整用量')
    if usage['prompt_tokens'] > record['request_bytes'] + 512 or usage['completion_tokens'] > record['max_tokens']:
        raise VerificationError('模型用量超过约定的输入或输出上限')
    if usage['prompt_tokens'] <= 0:
        raise VerificationError('非空验证输入没有计入用量')
    if (record['text_chars'] or record['thinking_chars']) and usage['completion_tokens'] <= 0:
        raise VerificationError('文字或推理输出没有计入用量')
    if record['thinking_tokens'] is not None and (type(record['thinking_tokens']) is not int
            or not 0 <= record['thinking_tokens'] <= usage['completion_tokens']):
        raise VerificationError('推理用量与总输出用量不一致')
    if type(record['json_schema_valid']) is not bool:
        raise VerificationError('结构化响应验证结果无效')
    for key in ('request_sha256', 'response_sha256'):
        value = record[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise VerificationError('验证指纹无效')
    if record['stop_reason'] not in ('end_turn', 'stop_sequence', 'max_tokens'):
        raise VerificationError('模型未按文字协议正常停止')


def validate_evidence(config, records, *, output_limit=OUTPUT_LIMIT, input_limit=INPUT_LIMIT):
    verification_profile(output_limit, input_limit)
    if not isinstance(records, list) or len(records) != 4:
        raise VerificationError('需要完成四个独立的合成验证场景')
    for record, (case, body) in zip(records, probe_requests(config, output_limit=output_limit, input_limit=input_limit)):
        check_observation(record, output_limit=output_limit, input_limit=input_limit)
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        if (record['case'] != case or record['request_bytes'] != len(payload)
                or record['max_tokens'] != body['max_tokens']
                or record['request_sha256'] != hashlib.sha256(payload).hexdigest()):
            raise VerificationError('验证场景、模型或请求内容指纹不一致')
        if case in ('cap16', 'cap128') and (record['stop_reason'] != 'max_tokens'
                or record['usage']['completion_tokens'] != body['max_tokens']):
            raise VerificationError('未观察到模型严格遵守输出截断上限')
        if case == 'json%d' % output_limit and (record['stop_reason'] != 'end_turn' or not record['json_schema_valid']):
            raise VerificationError('模型未生成完整的 JSON 工作记录')


class LocalBudgetProfiles:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / FILENAME

    def _read(self):
        if not self.path.exists():
            return dict(schema=1, entries=[])
        if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > MAX_FILE:
            raise VerificationError('本机模型验证档案不可读取')
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or set(value) != {'schema', 'entries'}
                or type(value['schema']) is not int or value['schema'] != 1
                or not isinstance(value['entries'], list) or len(value['entries']) > 8
                or any(not isinstance(r,dict) for r in value['entries'])):
            raise VerificationError('本机模型验证档案格式无效')
        return value

    def _write(self, value):
        if self.path.is_symlink() or self.data_dir.is_symlink():
            raise VerificationError('验证档案不能写入链接目录')
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.data_dir, prefix='.model-budget-verifications-', suffix='.tmp', delete=False) as out:
                temp = Path(out.name)
                out.write(canonical(value) + b'\n'); out.flush(); os.fsync(out.fileno())
            os.replace(temp, self.path)
        finally:
            if temp and temp.exists():
                temp.unlink()

    def record(self, config, records, now=None, *, output_limit=OUTPUT_LIMIT, input_limit=INPUT_LIMIT):
        profile = verification_profile(output_limit, input_limit)
        endpoint = identity(config)
        validate_evidence(config, records, output_limit=output_limit, input_limit=input_limit)
        checked = time.time() if now is None else now
        record = dict(profile=profile, identity=endpoint, verified_at=timestamp(checked),
            expires_at=timestamp(checked+TTL), status='verified', observations=records)
        if profile == EXTENDED_PROFILE:
            record['limits'] = dict(_PROFILE_LIMITS[profile])
        record['digest'] = fingerprint(record)
        with _LOCK:
            value = self._read()
            entries = [r for r in value['entries'] if r.get('identity') != endpoint]
            value['entries'] = (entries + [record])[-8:]
            self._write(value)
        return record

    def capability(self, config, now=None):
        try:
            endpoint = identity(config)
            with _LOCK:
                value = self._read()
            matching = [r for r in value['entries'] if r.get('identity') == endpoint]
            if len(matching) != 1:
                return None
            row = matching[0]
            profile = row.get('profile')
            if not isinstance(profile, str) or profile not in _PROFILE_LIMITS:
                return None
            limits = _PROFILE_LIMITS[profile]
            required = {'profile', 'identity', 'verified_at', 'expires_at', 'status', 'observations', 'digest'}
            if profile == EXTENDED_PROFILE:
                required.add('limits')
                saved_limits = row.get('limits')
                if (not isinstance(saved_limits, dict) or saved_limits != limits
                        or any(type(value) is not int for value in saved_limits.values())):
                    return None
            if set(row) != required or row['status'] != 'verified':
                return None
            if fingerprint({k:v for k,v in row.items() if k != 'digest'}) != row['digest']:
                return None
            start_date = dt.datetime.fromisoformat(row['verified_at'])
            expiry_date = dt.datetime.fromisoformat(row['expires_at'])
            if start_date.tzinfo is None or expiry_date.tzinfo is None:
                return None
            start, expiry = start_date.timestamp(), expiry_date.timestamp()
            current = time.time() if now is None else now
            if not start <= current < expiry or expiry-start != TTL:
                return None
            validate_evidence(config, row['observations'], output_limit=limits['max_output_tokens'],
                              input_limit=limits['max_request_bytes'])
            return dict(profile=profile, enforce_supported=True, input_bound='complete-request-utf8-bytes+512',
                output_parameter='max_tokens', local_verification=True,
                verified_at=row['verified_at'], expires_at=row['expires_at'], verification_id=row['digest'],
                **limits)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def revoke(self, config, reason='verification_started'):
        endpoint = identity(config)
        with _LOCK:
            value = self._read()
            changed = False
            for row in value['entries']:
                if row.get('identity') == endpoint:
                    row['status'] = 'revoked'
                    row['digest'] = fingerprint({k:v for k,v in row.items() if k != 'digest'})
                    changed = True
            if changed:
                self._write(value)


def run_verification(config, credential, progress=None, opener=None, *, output_limit=OUTPUT_LIMIT, input_limit=INPUT_LIMIT):
    """No redirects or automatic retries; no configurable prompt or source access."""
    from provider_bridge import NoRedirect, request_url
    verification_profile(output_limit, input_limit)
    endpoint = identity(config)
    if not isinstance(credential, str) or not credential or len(credential) > 8192 or any(ord(c)<33 or ord(c)>126 for c in credential):
        raise VerificationError('请先在小镇保存此服务的有效密钥')
    opener = opener or urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    records = []
    for case, body in probe_requests(config, output_limit=output_limit, input_limit=input_limit):
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type':'application/json', 'Accept':'application/json', 'anthropic-version':'2023-06-01'}
        if endpoint['auth_type'] == 'api_key':
            headers['x-api-key'] = credential
        else:
            headers['Authorization'] = 'Bearer ' + credential
        request = urllib.request.Request(request_url(config), data=payload, headers=headers, method='POST')
        if progress:
            progress(dict(case=case, status='running', max_tokens=body['max_tokens'], request_bytes=len(payload)))
        try:
            with opener.open(request, timeout=120) as response:
                raw = response.read(1000001)
                if len(raw) > 1000000:
                    raise VerificationError('验证回复过大，已停止')
                result = json.loads(raw.decode('utf-8'))
                record = observation(case, payload, body['max_tokens'], result, getattr(response,'status',200),
                                     output_limit=output_limit, input_limit=input_limit)
        except urllib.error.HTTPError as exc:
            code=exc.code; exc.close()
            raise VerificationError('模型验证请求未完成（HTTP %d），未授予预算适配资格' % code) from None
        except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException) as exc:
            if isinstance(exc, VerificationError):
                raise
            raise VerificationError('模型验证连接或响应无效，未授予预算适配资格') from None
        records.append(record)
        if progress:
            progress(dict(case=case, status='completed', **{k:record[k] for k in ('usage','stop_reason','text_chars','thinking_chars')}))
    validate_evidence(config, records, output_limit=output_limit, input_limit=input_limit)
    return records
