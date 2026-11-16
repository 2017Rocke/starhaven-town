#!/usr/bin/env python3
"""Explicit owner maintenance command; never sends project data or prints keys."""
import argparse
import http.client
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from credential_store import CredentialStore, CredentialError
from provider_bridge import ProviderBridge
from provider_verification import (EXTENDED_INPUT_LIMIT, EXTENDED_OUTPUT_LIMIT, INPUT_LIMIT, OUTPUT_LIMIT,
    LocalBudgetProfiles, VerificationError, run_verification, verification_profile)


def main(argv=None):
    parser = argparse.ArgumentParser(description='验证当前 Messages 服务的文字输出与用量上限；使用四次合成请求，不发送项目资料。')
    parser.add_argument('--data-dir', required=True, type=Path, help='本机小镇存档目录')
    parser.add_argument('--verify', action='store_true', help='实际发送合成请求，会消耗当前服务额度；不改变居民/源码授权')
    parser.add_argument('--output-limit', type=int, choices=(OUTPUT_LIMIT, EXTENDED_OUTPUT_LIMIT), default=OUTPUT_LIMIT,
                        help='验证的输出上限；扩展档位须同时指定 --input-limit 262144')
    parser.add_argument('--input-limit', type=int, choices=(INPUT_LIMIT, EXTENDED_INPUT_LIMIT), default=INPUT_LIMIT,
                        help='验证的完整请求字节上限；扩展档位须同时指定 --output-limit 32768')
    args = parser.parse_args(argv)
    try:
        verification_profile(args.output_limit, args.input_limit)
    except VerificationError as error:
        parser.error(str(error))
    data = args.data_dir.expanduser().resolve()
    try:
        config_path=data/'provider.json'
        config_bytes=config_path.read_bytes()
        config = ProviderBridge._validate(json.loads(config_bytes))
        profiles=LocalBudgetProfiles(data)
        if not args.verify:
            print(json.dumps(profiles.capability(config) or {'enforce_supported':False},ensure_ascii=False,indent=2))
            return 0
        profiles.revoke(config)
        key = CredentialStore(data).get(config['base_url'])
        records = run_verification(config, key, progress=lambda r:print(json.dumps(r,ensure_ascii=False),flush=True),
                                   output_limit=args.output_limit, input_limit=args.input_limit)
        if config_path.read_bytes() != config_bytes:
            raise VerificationError('验证期间模型设置发生变化，请按当前设置重新验证')
        profiles.record(config, records, output_limit=args.output_limit, input_limit=args.input_limit)
        print(json.dumps(dict(status='verified',total_reported_tokens=sum(r['usage']['total_tokens'] for r in records),
            capability=profiles.capability(config)),ensure_ascii=False,indent=2))
        return 0
    except (VerificationError, CredentialError, OSError, ValueError, http.client.HTTPException):
        # Credential and upstream exceptions can contain private data; only our
        # fixed local errors may be printed, never tracebacks or provider bodies.
        error=sys.exc_info()[1]
        print(str(error) if isinstance(error, VerificationError) else '验证未完成，请检查模型配置、凭据或本机文件权限。',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
