import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient} from '../web/voice_client.js';

class FakeRoom {
  constructor(options){this.options=options;this.handlers=new Map();this.calls=[];FakeRoom.latest=this;this.localParticipant={setMicrophoneEnabled:async enabled=>this.calls.push(['microphone',enabled])};}
  on(kind,handler){this.handlers.set(kind,handler);return this;}
  async startAudio(){this.calls.push(['unlock_audio']);}
  async connect(url,token){this.calls.push(['connect',url,token]);}
  async disconnect(){this.calls.push(['disconnect']);this.handlers.get('disconnected')?.();}
}
const RoomEvent={TrackSubscribed:'track',TrackUnsubscribed:'untrack',DataReceived:'data',AudioPlaybackStatusChanged:'audio',Disconnected:'disconnected'};
const sdk={Room:FakeRoom,RoomEvent,Track:{Kind:{Audio:'audio'}}};
function fixture(api=async()=>({server_url:'wss://test',participant_token:'temporary-token',state:{session_id:'s1'}})){
  const events=[],status=[],connections=[],tracks=[];
  const client=new VoiceClient({sdk,api,onEvent:e=>events.push(e),onStatus:s=>status.push(s),onConnection:c=>connections.push(c),attachAudio:t=>tracks.push(t),detachAudio:()=>tracks.splice(0)});
  return {client,events,status,connections,tracks};
}

test('connects to the issued room and publishes microphone with echo cancellation',async()=>{
  const f=fixture();await f.client.connect('s1');const room=FakeRoom.latest;
  assert.deepEqual(room.calls,[['unlock_audio'],['connect','wss://test','temporary-token'],['microphone',true]]);
  assert.equal(room.options.audioCaptureDefaults.echoCancellation,true);
  assert.equal(f.connections.at(-1),true);
});
test('attaches only subscribed remote audio',async()=>{
  const f=fixture();await f.client.connect('s1');const receive=FakeRoom.latest.handlers.get('track');
  receive({kind:'video'});receive({kind:'audio',id:'agent-audio'});assert.equal(f.tracks.length,1);
});
test('ignores foreign topics and sessions',async()=>{
  const f=fixture();await f.client.connect('s1');f.events.length=0;
  const receive=FakeRoom.latest.handlers.get('data'),packet=session=>new TextEncoder().encode(JSON.stringify({type:'offer_completed',session_id:session}));
  receive(packet('s2'),{},0,'clinic.events');receive(packet('s1'),{},0,'other');receive(packet('s1'),{},0,'clinic.events');
  assert.equal(f.events.length,1);
});
test('releases a failed connection without silently switching speech providers',async()=>{
  const f=fixture(async()=>{throw new Error('Missing LiveKit credentials');});
  await assert.rejects(f.client.connect('s1'),/credentials/);
  assert.equal(f.client.room,null);assert.equal(f.connections.at(-1),false);
  assert.deepEqual(FakeRoom.latest.calls,[['unlock_audio'],['disconnect']]);
});
test('mute, unmute, and disconnect control the actual published microphone',async()=>{
  const f=fixture();await f.client.connect('s1');const room=FakeRoom.latest;
  assert.equal(await f.client.toggleMute(),true);assert.equal(await f.client.toggleMute(),false);
  await f.client.disconnect();assert.equal(f.client.room,null);
  assert.deepEqual(room.calls.slice(-3),[['microphone',false],['microphone',true],['disconnect']]);
});
test('an active call cannot open a second room',async()=>{
  const f=fixture();await f.client.connect('s1');await assert.rejects(f.client.connect('s1'),/already active/);
});
