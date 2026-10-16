"""Real-browser checks for local UI and explicit missing-credential behavior."""
import argparse
import json
import sys
import tempfile
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from booking import LOCAL,Store
from server import handler_for
from voice_gateway import VoiceGateway


def main():
    from playwright.sync_api import sync_playwright,expect
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser-config',type=Path)
    parser.add_argument('--record-dir',type=Path,help='Record a typed local walkthrough, without microphone or provider audio')
    args=parser.parse_args()
    configuration=json.loads(args.browser_config.read_text()) if args.browser_config else {}
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'browser.sqlite',now=lambda:datetime(2026,10,14,12,tzinfo=LOCAL))
        handler=handler_for(store,VoiceGateway('','',''))
        handler.log_message=lambda *args:None
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as runtime:
                browser=runtime.chromium.launch(headless=True,executable_path=configuration.get('executablePath'),args=configuration.get('args'))
                record_options = {'record_video_dir': str(args.record_dir), 'record_video_size': {'width':1280,'height':900}} if args.record_dir else {}
                context=browser.new_context(viewport={'width':1280,'height':900},**record_options)
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                if args.record_dir:
                    page.add_init_script("""window.addEventListener('DOMContentLoaded',()=>{
                        const label=document.createElement('div');
                        label.textContent='TYPED WALKTHROUGH · NO MICROPHONE AUDIO';
                        label.style.cssText='position:fixed;bottom:12px;left:12px;z-index:9999;background:#173a32;color:white;padding:10px 14px;border-radius:8px;font:12px sans-serif;box-shadow:0 2px 8px #0003';
                        document.body.append(label);
                    })""")
                page.goto(f'http://127.0.0.1:{server.server_port}')
                def pause():
                    if args.record_dir:page.wait_for_timeout(2200)
                def send(text):
                    page.locator('#input').fill(text)
                    page.locator('#send').click()
                    expect(page.locator('.msg.assistant').last).not_to_have_text(previous[0])
                    previous[0]=page.locator('.msg.assistant').last.inner_text()
                    pause()
                def snapshot():
                    sid=page.evaluate("localStorage.getItem('monday.sessionId')")
                    return page.request.get(f'http://127.0.0.1:{server.server_port}/api/session?id={sid}').json()
                expect(page.locator('#count')).to_have_text('0')
                previous=[page.locator('.msg.assistant').last.inner_text()]
                pause()
                page.locator('[data-example]').first.click()
                expect(page.locator('#offer')).to_contain_text('Monday')
                expect(page.locator('#count')).to_have_text('0')
                previous[0]=page.locator('.msg.assistant').last.inner_text()
                pause()
                send('Actually, make that 10 AM')
                assert snapshot()['preferred_time']=='10:00'
                expect(page.locator('#offer')).to_contain_text('10:00')
                before=snapshot()
                send('Is that Monday?')
                after=snapshot()
                assert before['proposal_id']==after['proposal_id']
                assert before['revision']==after['revision']
                assert after['confirmed_proposal'] is None
                page.locator('[data-example="yes"]').click()
                expect(page.locator('#count')).to_have_text('1')
                booking=page.locator('#booking').inner_text()
                pause()
                previous[0]=page.locator('.msg.assistant').last.inner_text()
                send('yes')
                expect(page.locator('#booking')).to_have_text(booking)
                expect(page.locator('#count')).to_have_text('1')
                page.reload();expect(page.locator('#booking')).to_have_text(booking)
                pause()
                page.locator('#mic').click()
                expect(page.locator('#micStatus')).to_contain_text('Add LiveKit credentials')
                expect(page.locator('#mic')).to_be_enabled()
                expect(page.locator('#count')).to_have_text('1')
                page.locator('#newSession').click()
                expect(page.locator('#count')).to_have_text('0')
                previous[0]=page.locator('.msg.assistant').last.inner_text()
                send('Monday at 11 AM')
                assert snapshot()['offer'] is None
                assert snapshot()['preferred_time']=='11:00'
                expect(page.locator('.msg.assistant').last).to_contain_text('No mock dermatologist slots match')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2'), 'Mobile layout overflows'
                assert not errors,errors
                print(json.dumps({'checks':{'local_booking':True,'reload_same_reference':True,
                    'real_client_missing_credentials':True,'mobile_layout':True,
                    'exact_time':True,'date_clarification_preserves_offer':True,'unavailable_time_rejected':True},
                    'page_errors':errors,'live_webrtc_connected':False}))
                video=page.video
                context.close()
                if args.record_dir:print(json.dumps({'typed_walkthrough_video':str(video.path()),'microphone_audio':False}))
                browser.close()
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
