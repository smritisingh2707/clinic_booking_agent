import {Room, RoomEvent, Track} from 'livekit-client';

export class VoiceClient {
  constructor({api, onEvent, onStatus, onConnection, attachAudio, detachAudio,
               sdk={Room,RoomEvent,Track}}) {
    Object.assign(this,{api,onEvent,onStatus,onConnection,attachAudio,detachAudio,sdk});
    this.room=null;
    this.sessionId=null;
    this.muted=false;
    this.connecting=false;
  }

  async connect(sessionId) {
    if(this.room||this.connecting)throw new Error('A voice connection is already active.');
    this.connecting=true;
    const {Room,RoomEvent,Track}=this.sdk;
    const room=new Room({audioCaptureDefaults:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    this.room=room;
    room.on(RoomEvent.TrackSubscribed,(track)=>{
      if(track.kind===Track.Kind.Audio)this.attachAudio(track);
    });
    room.on(RoomEvent.TrackUnsubscribed,(track)=>this.detachAudio(track));
    room.on(RoomEvent.DataReceived,(bytes,participant,kind,topic)=>{
      if(topic!=='clinic.events')return;
      try {
        const packet=JSON.parse(new TextDecoder().decode(bytes));
        if(packet.session_id===this.sessionId)this.onEvent(packet);
      }catch {this.onStatus('An invalid voice event was ignored.');}
    });
    room.on(RoomEvent.AudioPlaybackStatusChanged,()=>{
      if(!room.canPlaybackAudio)this.onStatus('Click Enable audio to hear the agent.');
    });
    room.on(RoomEvent.Disconnected,()=>{
      this.detachAudio();this.onConnection(false);this.onStatus('Voice disconnected.');
      if(this.room===room)this.room=null;
    });
    try {
      // Unlock browser audio during the button gesture, before network requests.
      await room.startAudio();
      this.onStatus('Connecting to LiveKit…');
      const connection=await this.api('/api/voice/connect',{session_id:sessionId});
      this.sessionId=connection.state.session_id;
      this.onEvent({type:'booking_state',session_id:this.sessionId,state:connection.state});
      await room.connect(connection.server_url,connection.participant_token);
      await room.localParticipant.setMicrophoneEnabled(true);
      this.muted=false;
      this.onConnection(true);
      this.onStatus('Connected. Speak to the clinic agent.');
    }catch(error) {
      await room.disconnect();
      if(this.room===room)this.room=null;
      this.onConnection(false);
      this.onStatus(error.message);
      throw error;
    }finally {this.connecting=false;}
  }

  async disconnect() {
    const room=this.room;this.room=null;
    if(room)await room.disconnect();
    this.detachAudio();this.onConnection(false);
  }

  async toggleMute() {
    if(!this.room)throw new Error('Connect voice first.');
    this.muted=!this.muted;
    await this.room.localParticipant.setMicrophoneEnabled(!this.muted);
    return this.muted;
  }

  async enableAudio() {
    if(this.room)await this.room.startAudio();
  }
}
