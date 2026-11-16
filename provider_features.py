"""Local feature evidence, separate from model/network/source authorization."""
import base64
import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from provider_verification import canonical, fingerprint, timestamp

FILENAME = "model-feature-verifications.json"
FEATURE_LIMITS = dict(max_images=4, max_image_pixels=2359296, max_image_bytes=1500000,
                      image_input_allowance=16384, vision_max_request_bytes=8388608)
_LOCK = threading.RLock()


def clean_images(images):
    if not isinstance(images, list) or len(images) > FEATURE_LIMITS['max_images']:
        raise RuntimeError("请将图片拆成每批最多4张后分析。")
    result = []
    for item in images:
        if not isinstance(item, dict) or set(item) - {'id', 'mime_type', 'data'}:
            raise RuntimeError("图片调用参数无效。")
        if item.get('mime_type') not in ('image/png', 'image/jpeg') or not isinstance(item.get('data'), str):
            raise RuntimeError("模型图片必须使用已解析的PNG或JPEG。")
        try:
            if len(item['data']) > 2000000: raise ValueError()
            raw = base64.b64decode(item['data'], validate=True)
            from PIL import Image
            import io
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > FEATURE_LIMITS['max_image_pixels'] or max(image.size) > 2048:
                    raise ValueError()
                expected = 'PNG' if item['mime_type'] == 'image/png' else 'JPEG'
                if image.format != expected: raise ValueError()
                image.verify()
            if not raw or len(raw) > FEATURE_LIMITS['max_image_bytes']: raise ValueError()
        except ImportError:
            raise RuntimeError("当前程序缺少图片解析组件，请安装完整的小镇更新。") from None
        except Exception:
            raise RuntimeError("图片损坏或超过本步图片规格，请重新上传或拆分图片。") from None
        result.append(dict(id=str(item.get('id', 'image'))[:100], mime_type=item['mime_type'], data=item['data']))
    return result


def add_images(body, images, protocol):
    if not images: return body
    body = copy.deepcopy(body)
    message = body['messages'][-1]
    blocks = [dict(type='text', text=message['content'])]
    for item in images:
        if protocol == 'anthropic':
            blocks.append(dict(type='image', source=dict(type='base64', media_type=item['mime_type'], data=item['data'])))
        else:
            blocks.append(dict(type='image_url', image_url=dict(url='data:' + item['mime_type'] + ';base64,' + item['data'], detail='high')))
    message['content'] = blocks
    return body


def text_body(body):
    """Keep auditable text; sanitized local image bytes are not text/URLs."""
    body = copy.deepcopy(body)
    for message in body.get('messages', []):
        if not isinstance(message.get('content'), list): continue
        for block in message['content']:
            if isinstance(block, dict) and block.get('type') in ('image', 'image_url'):
                block.clear(); block.update(type='image', omitted=True)
    return body


def input_bound(payload):
    """Count textual framing and a verified allowance per sanitized image."""
    try:
        body = json.loads(payload)
    except (ValueError, TypeError):
        return len(payload) + 512
    if not isinstance(body, dict): return len(payload) + 512
    count = 0
    for message in body.get('messages', []):
        content = message.get('content')
        if not isinstance(content, list): continue
        for block in content:
            if not isinstance(block, dict): continue
            if block.get('type') in ('image', 'image_url'):
                count += 1
                block.clear(); block.update(type='image', omitted=True)
    if not count: return len(payload) + 512
    return len(json.dumps(body, ensure_ascii=False).encode('utf-8')) + 512 + count * FEATURE_LIMITS['image_input_allowance']


class FeatureProfiles:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.path = self.root / FILENAME

    def _read(self):
        if not self.path.exists(): return dict(schema=1, entries=[])
        if self.path.is_symlink() or self.path.stat().st_size > 131072: raise ValueError('invalid_feature_file')
        value = json.loads(self.path.read_text())
        if (not isinstance(value, dict) or set(value) != {'schema','entries'} or value['schema'] != 1
                or not isinstance(value['entries'], list) or len(value['entries']) > 16):
            raise ValueError('invalid_feature_file')
        return value

    def _write(self, value):
        if self.root.is_symlink() or self.path.is_symlink(): raise ValueError('invalid_feature_path')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix='.model-feature-', delete=False) as out:
                temporary = Path(out.name); out.write(canonical(value)); out.flush(); os.fsync(out.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists(): temporary.unlink()

    @staticmethod
    def validate_observations(feature, observations):
        if feature not in ('streaming', 'vision') or not isinstance(observations, list) or len(observations) != 2:
            raise ValueError('invalid_feature_evidence')
        for item in observations:
            if (not isinstance(item, dict) or set(item) != {'case','passed','input_tokens','output_tokens'}
                    or item['passed'] is not True or any(type(item[k]) is not int or not 0 < item[k] <= 1000000 for k in ('input_tokens','output_tokens'))):
                raise ValueError('invalid_feature_evidence')
        expected = ['stream-cap', 'stream-json'] if feature == 'streaming' else ['vision-1', 'vision-2']
        if [x['case'] for x in observations] != expected: raise ValueError('invalid_feature_cases')

    def record(self, config, feature, observations, clock=None):
        from provider_bridge import endpoint_identity
        self.validate_observations(feature, observations)
        clock = time.time() if clock is None else clock
        row = dict(identity=endpoint_identity(config), feature=feature, status='verified',
            verified_at=timestamp(clock), expires_at=timestamp(clock+30*86400), observations=observations,
            limits=FEATURE_LIMITS if feature == 'vision' else {})
        row['digest'] = fingerprint(row)
        with _LOCK:
            value = self._read()
            value['entries'] = [x for x in value['entries'] if not (x.get('identity') == row['identity'] and x.get('feature') == feature)]
            value['entries'] = (value['entries'] + [row])[-16:]
            self._write(value)

    def capability(self, config, clock=None):
        from provider_bridge import endpoint_identity
        result = dict(supports_streaming=False, supports_vision=False)
        try:
            with _LOCK: entries = self._read()['entries']
            identity = endpoint_identity(config)
            clock = time.time() if clock is None else clock
            current_date = dt.datetime.fromtimestamp(clock, dt.timezone.utc)
            for feature in ('streaming','vision'):
                matching = [r for r in entries if r.get('identity') == identity and r.get('feature') == feature]
                if len(matching) != 1: continue
                row = matching[0]
                if set(row) != {'identity','feature','status','verified_at','expires_at','observations','limits','digest'}: continue
                if row['status'] != 'verified' or fingerprint({k:v for k,v in row.items() if k!='digest'}) != row['digest']: continue
                start = dt.datetime.fromisoformat(row['verified_at']); end = dt.datetime.fromisoformat(row['expires_at'])
                if (start.tzinfo is None or end.tzinfo is None or not start <= current_date < end
                        or end-start != dt.timedelta(days=30)): continue
                limits = FEATURE_LIMITS if feature == 'vision' else {}
                if row['limits'] != limits: continue
                try: self.validate_observations(feature, row['observations'])
                except ValueError: continue
                result['supports_'+feature] = True
                if feature == 'vision': result.update(limits)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return dict(supports_streaming=False, supports_vision=False)
        return result

    def revoke(self, config, feature=None):
        from provider_bridge import endpoint_identity
        with _LOCK:
            value = self._read()
            value['entries'] = [r for r in value['entries'] if r.get('identity') != endpoint_identity(config)
                                or (feature is not None and r.get('feature') != feature)]
            self._write(value)
