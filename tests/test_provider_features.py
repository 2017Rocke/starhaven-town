"""Streaming and multimodal boundaries; synthetic payloads and mocked transport only."""
import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from provider_features import FeatureProfiles, FEATURE_LIMITS, clean_images, add_images, input_bound
from provider_streaming import read_stream, StreamIncomplete
from provider_verification import fingerprint
from test_provider_response import ProviderResponseTest


def sse(events):
    return ''.join('data: ' + (item if isinstance(item, str) else json.dumps(item)) + '\n\n' for item in events).encode()


def anthropic_events():
    return [dict(type='message_start',message=dict(usage=dict(input_tokens=12,output_tokens=0))),
            dict(type='content_block_start',index=0,content_block=dict(type='thinking',thinking='')),
            dict(type='content_block_delta',index=0,delta=dict(type='thinking_delta',thinking='hidden-synthetic')),
            dict(type='content_block_stop',index=0),
            dict(type='content_block_start',index=1,content_block=dict(type='text',text='')),
            dict(type='content_block_delta',index=1,delta=dict(type='text_delta',text='{"ok":true}')),
            dict(type='content_block_stop',index=1),
            dict(type='message_delta',delta=dict(stop_reason='end_turn'),usage=dict(output_tokens=9)),
            dict(type='message_stop')]


def openai_events():
    return [dict(choices=[dict(index=0,delta=dict(reasoning_content='hidden-synthetic'))]),
            dict(choices=[dict(index=0,delta=dict(content='{"ok":true}'))]),
            dict(choices=[dict(index=0,delta={},finish_reason='stop')]),
            dict(choices=[],usage=dict(prompt_tokens=12,completion_tokens=9,total_tokens=21)), '[DONE]']


class StreamingTests(unittest.TestCase):
    def test_both_protocols_keep_usage_and_only_public_text(self):
        for protocol, events in [('anthropic',anthropic_events()),('openai',openai_events())]:
            progress=[]
            value, meta=read_stream(io.BytesIO(sse(events)),protocol,threading.Event(),progress.append)
            self.assertNotIn('hidden-synthetic',json.dumps(value)+json.dumps(progress))
            self.assertEqual(meta['thinking_chars'],16)
            self.assertEqual(meta['text_chars'],11)
            self.assertTrue(all(set(item)=={'phase','text_chars'} for item in progress))
            self.assertIsInstance(value['usage'],dict)

    def test_eof_cancellation_and_partial_stream_are_not_success(self):
        for protocol, events in [('anthropic',anthropic_events()),('openai',openai_events())]:
            for cut in range(len(events)):
                with self.assertRaises(StreamIncomplete):
                    read_stream(io.BytesIO(sse(events[:cut])),protocol,threading.Event())
        cancelled=threading.Event();cancelled.set()
        with self.assertRaises(RuntimeError):
            read_stream(io.BytesIO(sse(openai_events())),'openai',cancelled)

    def test_rejects_tools_bad_order_and_limits(self):
        invalid = [
            [anthropic_events()[0],dict(type='content_block_start',index=0,content_block=dict(type='tool_use'))],
            anthropic_events()[:3]+[anthropic_events()[7]],
            anthropic_events()[:4]+[anthropic_events()[2]],
            [dict(type='message_stop')],
            [dict(type='error',error=dict(message='private-upstream-text'))],
        ]
        for events in invalid:
            with self.assertRaises(StreamIncomplete) as failure:
                read_stream(io.BytesIO(sse(events)),'anthropic',threading.Event())
            self.assertNotIn('private-upstream-text',str(failure.exception))
        with self.assertRaises(StreamIncomplete):
            read_stream(io.BytesIO(sse(openai_events())),'openai',threading.Event(),max_bytes=10)
        with self.assertRaises(StreamIncomplete):
            read_stream(io.BytesIO(sse([dict(choices=[dict(index=0,delta=dict(tool_calls=[{}]))])])),'openai',threading.Event())


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.profile=FeatureProfiles(Path(self.temp.name))
        self.config=dict(mode='api',base_url='https://feature.example/v1',model='sample',protocol='openai',auth_type='bearer')

    def evidence(self,feature):
        cases=['stream-cap','stream-json'] if feature=='streaming' else ['vision-1','vision-2']
        return [dict(case=case,passed=True,input_tokens=15,output_tokens=16) for case in cases]

    def test_receipts_bound_to_identity_expiry_and_typed_evidence(self):
        self.profile.record(self.config,'streaming',self.evidence('streaming'),clock=1000)
        self.assertTrue(self.profile.capability(self.config,clock=1001)['supports_streaming'])
        self.assertFalse(self.profile.capability(dict(self.config,model='changed'),clock=1001)['supports_streaming'])
        self.assertFalse(self.profile.capability(self.config,clock=1000+30*86400)['supports_streaming'])
        value=json.loads(self.profile.path.read_text());row=value['entries'][0]
        row['observations'][0]['input_tokens']=True
        row['digest']=fingerprint({k:v for k,v in row.items() if k!='digest'})
        self.profile.path.write_text(json.dumps(value))
        self.assertFalse(self.profile.capability(self.config,clock=1001)['supports_streaming'])

    def test_independent_revocation_and_private_file_mode(self):
        for feature in ('streaming','vision'):self.profile.record(self.config,feature,self.evidence(feature))
        self.profile.revoke(self.config,'vision')
        self.assertEqual(self.profile.capability(self.config),dict(supports_streaming=True,supports_vision=False))
        import os
        if os.name != 'nt':self.assertEqual(self.profile.path.stat().st_mode & 0o777,0o600)

    def test_image_bytes_and_token_reservation(self):
        try:from PIL import Image
        except ImportError:self.skipTest('Install requirements-runtime.txt for image tests')
        out=io.BytesIO();Image.new('RGB',(64,64),'red').save(out,'PNG')
        item=dict(id='image',mime_type='image/png',data=base64.b64encode(out.getvalue()).decode())
        images=clean_images([item])
        for protocol in ('anthropic','openai'):
            body=add_images(dict(messages=[dict(role='user',content='Synthetic image')]),images,protocol)
            payload=json.dumps(body).encode()
            self.assertGreaterEqual(input_bound(payload),FEATURE_LIMITS['image_input_allowance'])
            self.assertLess(input_bound(payload),FEATURE_LIMITS['image_input_allowance']+1024)
        for bad in (dict(item,mime_type='image/jpeg'),dict(item,data='not-base64'),dict(item,url='https://example.com/a.png')):
            with self.assertRaises(RuntimeError):clean_images([bad])
        with self.assertRaises(RuntimeError):clean_images([item]*5)
        self.assertEqual(input_bound(b'synthetic text'),len(b'synthetic text')+512)


class StreamResponse(io.BytesIO):
    headers={'Content-Type':'text/event-stream'}


class StreamingBridgeTests(ProviderResponseTest):
    def test_stream_bridge_uses_verified_profile_and_public_text(self):
        from test_provider_response import ResponseBudget
        self.bridge.set_budget(ResponseBudget(enforce=True))
        self.bridge._feature_profiles.record(self.config,'streaming',FeatureTests.evidence(self,'streaming'))
        with patch.object(self.opener,'open',return_value=StreamResponse(sse(openai_events()))) as sent:
            result=self.bridge.generate_result('Synthetic',threading.Event(),'resident',dict(controlled=True,stream=True))
        self.assertEqual(result['text'],'{"ok":true}')
        body=json.loads(sent.call_args[0][0].data)
        self.assertTrue(body['stream']);self.assertTrue(body['stream_options']['include_usage'])

    def test_partial_stream_fails_and_preserves_unknown_usage(self):
        from test_provider_response import ResponseBudget
        budget=ResponseBudget(enforce=True);self.bridge.set_budget(budget)
        self.bridge._feature_profiles.record(self.config,'streaming',FeatureTests.evidence(self,'streaming'))
        with patch.object(self.opener,'open',return_value=StreamResponse(sse(openai_events()[:-1]))):
            with self.assertRaisesRegex(RuntimeError,'中断'):
                self.bridge.generate_result('Synthetic',threading.Event(),'resident',dict(controlled=True,stream=True))
        self.assertFalse(budget.settled[-1].get('response_received',False))
        self.assertNotIn('reply',budget.settled[-1])
