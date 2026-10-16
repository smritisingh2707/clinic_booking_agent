import json
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from booking import LOCAL,Store
from server import handler_for
from voice_gateway import VoiceGateway,agent_key


class VoiceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'clinic.sqlite',now=lambda:datetime(2026,10,14,12,tzinfo=LOCAL))
        self.secret='fixture-secret-which-is-more-than-32-bytes'
        self.gateway=VoiceGateway('ws://127.0.0.1:7880','fixture-key',self.secret)
        handler=handler_for(self.store,self.gateway)
        handler.log_message=lambda *args:None
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.sid=self.store.new_session(voice=True)['session_id']
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def post(self,path,data,authorized=False):
        headers={'Content-Type':'application/json'}
        if authorized:headers['X-Voice-Agent-Key']=agent_key(self.secret)
        with urlopen(Request(self.base+path,data=json.dumps(data).encode(),headers=headers)) as response:return json.load(response)
    def test_browser_cannot_fake_spoken_confirmation_or_playout(self):
        for path,args in [('/api/observe',{'session_id':self.sid,'text':'yes','turn_id':'fake','sequence':1}),
                          ('/api/voice/played',{'session_id':self.sid,'proposal_id':'fake','revision':0}),
                          ('/api/session',{'voice':True})]:
            with self.assertRaises(HTTPError) as raised:self.post(path,args)
            self.assertEqual(raised.exception.code,403)
    def test_text_route_cannot_bypass_a_voice_session(self):
        with self.assertRaises(HTTPError) as raised:
            self.post('/api/chat',{'session_id':self.sid,'text':'Monday','turn_id':'text','sequence':1})
        self.assertEqual(raised.exception.code,409)
    def test_authenticated_agent_journey_requires_completed_playout(self):
        state=self.post('/api/observe',{'session_id':self.sid,'text':'Monday','turn_id':'one','sequence':1},True)
        slots=self.post('/api/availability',{'session_id':self.sid,'revision':state['revision']})
        offer=self.post('/api/propose',{'session_id':self.sid,'revision':state['revision'],'slot_id':slots['slots'][0]['id']})
        approval={'session_id':self.sid,'text':'yes','turn_id':'two','sequence':2,'proposal_id':offer['proposal_id']}
        with self.assertRaises(HTTPError) as raised:self.post('/api/observe',approval,True)
        self.assertEqual(raised.exception.code,409)
        self.post('/api/voice/played',{'session_id':self.sid,'proposal_id':offer['proposal_id'],'revision':state['revision']},True)
        self.post('/api/observe',approval,True)
        payload={'session_id':self.sid,'proposal_id':offer['proposal_id']}
        one=self.post('/api/book',payload);two=self.post('/api/book',payload)
        self.assertEqual(one['id'],two['id'])
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'],1)
    def test_missing_credentials_is_explicit_and_does_not_issue_a_token(self):
        missing=VoiceGateway('','','')
        self.assertFalse(missing.config()['configured'])
        from booking import BookingError
        with self.assertRaises(BookingError) as raised:missing.connect(self.store,self.sid)
        self.assertEqual(raised.exception.status,503)
    def test_issued_token_is_short_lived_room_scoped_and_dispatches_the_same_session(self):
        try:import jwt
        except ImportError:self.skipTest('Install voice dependencies for actual JWT validation')
        connection=self.post('/api/voice/connect',{'session_id':self.sid})
        claims=jwt.decode(connection['participant_token'],self.secret,algorithms=['HS256'])
        self.assertEqual(claims['video']['room'],connection['room_name'])
        self.assertEqual(claims['video']['canPublishSources'],['microphone'])
        self.assertFalse(claims['video']['canPublishData'])
        self.assertLessEqual(claims['exp']-claims['nbf'],300)
        dispatch=claims['roomConfig']['agents'][0]
        self.assertEqual(dispatch['agentName'],'clinic-booking')
        self.assertEqual(json.loads(dispatch['metadata'])['session_id'],self.sid)
        self.assertNotIn(self.secret,json.dumps(connection))
        self.assertNotIn(agent_key(self.secret),connection['participant_token'])
