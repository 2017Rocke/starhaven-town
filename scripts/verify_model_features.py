#!/usr/bin/env python3
"""Explicit synthetic streaming/vision checks, never user files or project data."""
import argparse
import base64
import io
import json
import re
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from credential_store import CredentialStore
from provider_bridge import (ProviderBridge, NoRedirect, request_url, _protocol_options,
                             _anthropic_usage, _anthropic_text, _openai_text, _response_usage_violation)
from provider_features import FeatureProfiles, clean_images, add_images
from provider_streaming import read_stream


def synthetic_cases(config, feature):
    protocol, _ = _protocol_options(config)
    if feature == 'streaming':
        cases = [('stream-cap', 'Write integers from 1 to 1000, one per line, without explanation.', 16, None),
                 ('stream-json', 'Return only this JSON object: {"status":"ok","items":["sample"]}.', 8192, None)]
    else:
        from PIL import Image, ImageDraw
        cases = []
        for index, (color, shape) in enumerate((('red', 'square'), ('blue', 'circle')), 1):
            pic = Image.new('RGB', (1536, 1536), 'white')
            draw = ImageDraw.Draw(pic)
            (draw.rectangle if shape == 'square' else draw.ellipse)((256, 256, 1280, 1280), fill=color)
            stream = io.BytesIO(); pic.save(stream, 'PNG'); pic.close()
            attachment = dict(id='synthetic', mime_type='image/png', data=base64.b64encode(stream.getvalue()).decode('ascii'))
            prompt = ('Identify the single large colored shape in this image. Return ONLY JSON '
                      'with keys "color" (red, blue, green or yellow) and "shape" (circle, square or triangle).')
            cases.append(('vision-' + str(index), prompt, 8192, attachment))
    for case, prompt, cap, attachment in cases:
        body = dict(model=config['model'], max_tokens=cap, stream=feature == 'streaming',
                    messages=[dict(role='user', content=prompt)])
        if protocol == 'openai' and feature == 'streaming': body['stream_options'] = dict(include_usage=True)
        if attachment: body = add_images(body, clean_images([attachment]), protocol)
        yield case, body


def run_feature(config, key, feature, progress=None, opener=None, stream_vision=False, output_limit=8192):
    protocol, auth = _protocol_options(config)
    opener = opener or urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    records = []
    for case, body in synthetic_cases(config, feature):
        body['max_tokens'] = min(body['max_tokens'], output_limit)
        if feature == 'vision' and stream_vision:
            body['stream'] = True
            if protocol == 'openai': body['stream_options'] = dict(include_usage=True)
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream' if body['stream'] else 'application/json'}
        if protocol == 'anthropic': headers['anthropic-version'] = '2023-06-01'
        if key:
            if any(ord(c) < 33 or ord(c) > 126 for c in key) or len(key) > 8192: raise ValueError('invalid_key')
            headers['x-api-key' if auth == 'api_key' else 'Authorization'] = key if auth == 'api_key' else 'Bearer ' + key
        request = urllib.request.Request(request_url(config), data=payload, headers=headers, method='POST')
        with opener.open(request, timeout=120) as response:
            if getattr(response, 'status', 200) != 200: raise ValueError('unexpected_http_status')
            if body['stream']:
                if 'text/event-stream' not in response.headers.get('Content-Type', '').lower(): raise ValueError('stream_not_returned')
                result, _ = read_stream(response, protocol, threading.Event())
            else:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000: raise ValueError('response_too_large')
                result = json.loads(raw)
        usage = _anthropic_usage(result.get('usage')) if protocol == 'anthropic' else result.get('usage')
        violation = _response_usage_violation(usage, payload, body['max_tokens'])
        if violation: raise ValueError('usage_contract_' + violation)
        if usage['prompt_tokens'] <= 0 or usage['completion_tokens'] <= 0: raise ValueError('missing_measured_usage')
        stop = result.get('stop_reason') if protocol == 'anthropic' else result['choices'][0].get('finish_reason')
        if case == 'stream-cap':
            if stop != ('max_tokens' if protocol == 'anthropic' else 'length') or usage['completion_tokens'] != 16:
                raise ValueError('stream_cap_not_observed')
        else:
            if stop not in ('end_turn', 'stop'): raise ValueError('unfinished_reply')
            text = _anthropic_text(result, body['max_tokens']) if protocol == 'anthropic' else _openai_text(result)
            answer = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.I))
            expected = ({'status':'ok','items':['sample']} if case == 'stream-json' else
                        {'color':'red','shape':'square'} if case == 'vision-1' else {'color':'blue','shape':'circle'})
            if answer != expected: raise ValueError('synthetic_answer_mismatch')
        record = dict(case=case, passed=True, input_tokens=usage['prompt_tokens'], output_tokens=usage['completion_tokens'])
        records.append(record)
        if callable(progress): progress(record)
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description='用合成材料验证流式/看图兼容性，不发送本机项目资料。')
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--feature', choices=('streaming', 'vision', 'all'), default='all')
    parser.add_argument('--verify', action='store_true', help='向已配置服务发送每项两次测试，会消耗模型额度')
    args = parser.parse_args(argv)
    data = args.data_dir.expanduser().resolve()
    try:
        raw = (data / 'provider.json').read_bytes()
        config = ProviderBridge._validate(json.loads(raw))
        profiles = FeatureProfiles(data)
        if not args.verify:
            print(json.dumps(profiles.capability(config), ensure_ascii=False)); return 0
        bridge = ProviderBridge(data / 'runtime', data, CredentialStore(data))
        try: capability = bridge.resolved_budget_capability(config)
        finally: bridge.close()
        if not capability.get('enforce_supported') or capability.get('max_output_tokens', 0) < 4096:
            raise ValueError('verify_text_budget_first')
        key = CredentialStore(data).get(config['base_url'])
        failures = []
        for feature in ('streaming', 'vision') if args.feature == 'all' else (args.feature,):
            profiles.revoke(config, feature)
            try:
                records = run_feature(config, key, feature,
                    progress=lambda row: print(json.dumps(row), flush=True),
                    stream_vision=profiles.capability(config).get('supports_streaming', False),
                    output_limit=capability['max_output_tokens'])
                if (data / 'provider.json').read_bytes() != raw: raise ValueError('model_changed')
                profiles.record(config, feature, records)
                print(json.dumps(dict(feature=feature,status='verified')),flush=True)
            except Exception as error:
                # Do not print upstream bodies, credentials or arbitrary exceptions.
                failures.append(feature)
                print(json.dumps(dict(feature=feature,status='not_verified',
                    http_status=error.code if isinstance(error, urllib.error.HTTPError) else None,
                    error_type=type(error).__name__,
                    reason=str(error) if str(error) in ('synthetic_answer_mismatch','unfinished_reply',
                        'missing_measured_usage','usage_contract_missing_usage','usage_contract_invalid_usage',
                        'usage_contract_input_overrun','usage_contract_output_overrun','stream_not_returned') else 'compatibility_check_failed')),flush=True)
        print(json.dumps(profiles.capability(config), ensure_ascii=False), flush=True)
        return 1 if failures else 0
    except Exception:
        print('验证未完成，请检查模型设置、文字预算验证和依赖组件。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
