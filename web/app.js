let state=null, sequence=0, epoch=0, queue=Promise.resolve();
let voice=null, voiceActive=false, refreshTimer=null, voiceEvents=[];
const el=id=>document.getElementById(id);
async function api(path,data){
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const body=await response.json();
  if(!response.ok)throw new Error(body.error||'Request failed');
  return body;
}
function message(text,who='assistant'){
  if(!text)return;
  const node=document.createElement('div');node.className='msg '+who;node.textContent=text;
  el('messages').append(node);el('messages').scrollTop=el('messages').scrollHeight;
}
function stamp(value){return new Date(value).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',weekday:'long',day:'numeric',month:'short',hour:'numeric',minute:'2-digit'})+' IST';}
function render(next){
  state=next;sequence=Math.max(sequence,next.last_sequence);
  localStorage.setItem('monday.sessionId',next.session_id);
  el('requestDate').textContent=next.preferred_date?next.preferred_date+(next.preferred_time?' · '+next.preferred_time+' IST':''):'Choose a day';
  el('offer').textContent=next.offer?next.offer.doctor+' · '+stamp(next.offer.starts_at):'No slot is offered yet.';
  el('count').textContent=next.booking_count;
  el('booking').textContent=next.booking?'Confirmed · '+next.booking.id:'';
  el('input').disabled=!!next.voice_mode;el('send').disabled=!!next.voice_mode;
  document.querySelectorAll('[data-example]').forEach(button=>button.disabled=!!next.voice_mode);
  el('trace').replaceChildren();
  for(const event of next.events||[]){
    const item=document.createElement('li');item.textContent=event.kind.replaceAll('_',' ');
    const meta=document.createElement('span');meta.textContent=event.detail;
    item.append(meta);el('trace').append(item);
  }
  if(next.voice_mode)el('spokenOffer').textContent=next.played_proposal?'Ready for confirmation':'Awaiting completed offer';
}
async function refresh(sessionId){
  const response=await fetch('/api/session?id='+encodeURIComponent(sessionId));
  if(!response.ok)return;
  const recovered=await response.json();
  if(state?.session_id===sessionId&&recovered.last_sequence>=state.last_sequence)render(recovered);
}
async function newSession(){
  epoch++;if(voice)await voice.disconnect();
  const snapshot=await api('/api/session',{});
  sequence=0;voiceEvents=[];el('messages').replaceChildren();render(snapshot);
  el('spokenOffer').textContent='Awaiting offer';el('responseLatency').textContent='—';el('interruptionLatency').textContent='—';
  message('Which day would you like to see a dermatologist? You can correct the day before confirming.');
}
function send(text){
  if(!state||!text.trim()||state.voice_mode)return;
  const requestEpoch=++epoch, session=state.session_id, proposal=state.proposal_id, seq=++sequence, turnId=crypto.randomUUID();
  message(text,'user');el('input').value='';
  queue=queue.catch(()=>{}).then(async()=>{
    try{
      const next=await api('/api/chat',{session_id:session,text,turn_id:turnId,sequence:seq,proposal_id:proposal});
      if(state.session_id!==session)return;
      render(next);if(requestEpoch===epoch)message(next.message);
    }catch(error){
      if(requestEpoch===epoch){
        await refresh(session).catch(()=>{});
        message(state.booking?'The booking was recorded: '+state.booking.id+'.':error.message,'error');
      }
    }
  });
}
function voiceEvent(packet){
  voiceEvents.push({...packet,received_at:Date.now()});
  if(packet.type==='booking_state'){
    render({...state,...packet.state,events:state?.events||[]});
    refresh(packet.session_id).catch(()=>{});
  }
  if(packet.type==='user_turn')message(packet.text,'user');
  if(packet.type==='transcript')message(packet.text+(packet.interrupted?' [interrupted]':''));
  if(packet.type==='agent_state')el('voiceState').textContent=packet.state;
  if(packet.type==='offer_started')el('spokenOffer').textContent='Speaking offer…';
  if(packet.type==='offer_interrupted')el('spokenOffer').textContent='Interrupted · approval blocked';
  if(packet.type==='offer_completed')el('spokenOffer').textContent='Ready for confirmation';
  if(packet.type==='provider_error')message(packet.message,'error');
  if(packet.type==='latency'){
    if(packet.server_response_ms!==undefined)el('responseLatency').textContent=packet.server_response_ms+' ms';
    if(packet.server_interruption_ms!==undefined)el('interruptionLatency').textContent=packet.server_interruption_ms+' ms';
  }
}
function connectionChanged(active){
  voiceActive=active;el('mic').textContent=active?'Disconnect voice':'Connect voice';
  el('mute').disabled=!active;el('mute').textContent='Mute';
  clearInterval(refreshTimer);refreshTimer=null;
  if(active)refreshTimer=setInterval(()=>refresh(state.session_id).catch(()=>{}),1000);
  else {el('voiceState').textContent='Disconnected';el('enableAudio').hidden=true;}
}
async function connectVoice(){
  if(voiceActive){await voice.disconnect();return;}
  el('mic').disabled=true;el('newSession').disabled=true;
  try{
    const {VoiceClient}=await import('/voice_bundle.js');
    voice=new VoiceClient({
      api,onEvent:voiceEvent,onConnection:connectionChanged,
      onStatus:status=>{el('micStatus').textContent=status;if(status.includes('Enable audio'))el('enableAudio').hidden=false;},
      attachAudio:track=>{const audio=track.attach();audio.autoplay=true;el('agentAudio').append(audio);},
      detachAudio:track=>{if(track)track.detach().forEach(node=>node.remove());else el('agentAudio').replaceChildren();},
    });
    await voice.connect(state.session_id);
  }catch(error){message(error.message,'error');}
  finally{el('mic').disabled=false;el('newSession').disabled=false;}
}
el('chatForm').addEventListener('submit',event=>{event.preventDefault();send(el('input').value);});
el('input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send(el('input').value);}});
el('newSession').addEventListener('click',()=>newSession().catch(error=>message(error.message,'error')));
document.querySelectorAll('[data-example]').forEach(button=>button.addEventListener('click',()=>send(button.dataset.example)));
el('mic').addEventListener('click',connectVoice);
el('mute').addEventListener('click',async()=>{try{const muted=await voice.toggleMute();el('mute').textContent=muted?'Unmute':'Mute';}catch(error){message(error.message,'error');}});
el('enableAudio').addEventListener('click',async()=>{try{await voice.enableAudio();el('enableAudio').hidden=true;}catch(error){message(error.message,'error');}});
el('exportVoice').addEventListener('click',()=>{
  const blob=new Blob([JSON.stringify({session_id:state?.session_id,exported_at:new Date().toISOString(),events:voiceEvents},null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='clinic-voice-trace.json';link.click();URL.revokeObjectURL(url);
});
async function restore(){
  const saved=localStorage.getItem('monday.sessionId');
  if(saved){
    const response=await fetch('/api/session?id='+encodeURIComponent(saved));
    if(response.ok){const recovered=await response.json();render(recovered);message(recovered.booking?'Restored your booking: '+recovered.booking.id:recovered.voice_mode?'Voice session restored. Connect voice to continue.':'Session restored. Correct the day or confirm the current offer.');return;}
  }
  await newSession();
}
window.addEventListener('pagehide',()=>{clearInterval(refreshTimer);if(voice)void voice.disconnect();});
restore().catch(error=>message(error.message,'error'));
