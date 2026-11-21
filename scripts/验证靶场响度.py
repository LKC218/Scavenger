"""用改前混音函数和真实素材核对响度提升，并通过真实控件采样有声交战。"""
import argparse
import hashlib
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'local-data/原型/载具靶场/验证/响度增强1'


def run(channel, performance=False):
    OUT.mkdir(parents=True, exist_ok=True)
    result = {'channel': channel, 'headless': True, 'viewport': [1600, 1000], 'checks': [], 'errors': [], 'samples': []}
    result['source_identity'] = [{'path': p.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                 for p in sorted((ROOT/'src/载具靶场').glob('*')) if p.suffix in ['.mjs', '.html', '.css']]
    file = OUT/(channel+('-负载.json' if performance else '-响度.json'))
    def save(): file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    def passed(name): result['checks'].append(name); save(); print(channel+'：'+name, flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True)
        result['version'] = browser.version
        page = browser.new_page(viewport={'width': 1600, 'height': 1000}, device_scale_factor=1)
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        page.on('console', lambda m: result['errors'].append(m.text) if m.type == 'error' else None)
        page.on('response', lambda r: result['errors'].append(str(r.status)+' '+r.url) if r.status >= 400 else None)
        def wait(expression, timeout=15000): page.wait_for_function('() => '+expression, timeout=timeout)
        def value(expression): return page.evaluate('() => '+expression)
        try:
            page.goto('http://127.0.0.1:8768/', wait_until='networkidle'); wait('window.range?.ready', 40000)
            assert value('range.audio.masterVolume') == 1 and value('range.mixer.boostDb') == 6
            assert page.locator('#enhancement').input_value() == '6' and page.locator('#volume').input_value() == '100'
            page.locator('#sound').click(); wait('range.mixer.active && range.audio.stats().cached===13')
            wait('range.mixer.engines.size===8 && [...range.mixer.engines.values()].every(e=>e.sound?.isPlaying)')
            passed('初始总音量100%、增强6dB，真实手势解锁13声音')
            if not performance:
                before_path = OUT/'改前源码/声音.mjs'
                before = before_path.read_text('utf-8')
                identity = json.loads((OUT/'改前源码/身份.json').read_text('utf-8'))
                expected = next(r['sha256'] for r in identity if r['path'].endswith('声音.mjs'))
                assert hashlib.sha256(before_path.read_bytes()).hexdigest() == expected
                old_function = before[before.index('export function createMix'):before.index('/** 持续机械声')]
                page.add_script_tag(content=old_function.replace('export function createMix', 'window.beforeMix = function'))
                result['before_mix_sha256'] = expected
                result['waveforms'] = page.evaluate('''async()=>{
                  const {createMix}=await import('/声音.mjs'),rate=48000,length=rate*4;
                  async function render(key,distance,db,old=false,dense=false,synthetic=false,volume=1){
                    const c=new OfflineAudioContext(2,length,rate),input=c.createGain();input.gain.value=volume;
                    const mix=old?window.beforeMix(c,input):createMix(c,input,{boostDb:db});
                    const entries=dense?Array.from({length:48},(_,i)=>[Object.entries(range.manifest.audio).filter(([k])=>i<8?k.endsWith('Move'):!k.endsWith('Move'))[i%(i<8?3:10)],i]):[[[key,range.manifest.audio[key]],0]];
                    for(const [[name,asset],i] of entries){
                      const source=c.createBufferSource(),gain=c.createGain(),panner=c.createPanner();
                      if(synthetic){const b=c.createBuffer(1,length,rate),a=b.getChannelData(0);for(let n=0;n<a.length;n++)a[n]=Math.sin(n/rate*220*Math.PI*2)*.01;source.buffer=b;}
                      else source.buffer=range.audio.cache.get(asset.audio_id).buffer;
                      source.loop=!!asset.loop;
                      const kind=name.startsWith('tank')?'tank':name.startsWith('heli')?'heli':'plane';
                      gain.gain.value=synthetic?1:asset.loop?({tank:.28,heli:.28,plane:.42}[kind]):.65;
                      panner.panningModel='HRTF';panner.distanceModel='inverse';panner.refDistance=synthetic?100:asset.loop?({tank:45,heli:90,plane:140}[kind]):100;panner.rolloffFactor=1;
                      panner.positionX.value=dense?(i%5-2)*13:0;panner.positionY.value=synthetic?0:asset.loop?({tank:-7,heli:28,plane:65}[kind]):0;panner.positionZ.value=synthetic?0:distance+(dense?i%4*17:0);
                      source.connect(panner);panner.connect(gain);gain.connect(input);
                      source.start(dense?(i%7)*.018:0,asset.loop?.9+(dense?i%5*.27:0):0);
                    }
                    const output=await c.startRendering();let sum=0,peak=0,above94=0,invalid=0,windows=[];
                    for(let ch=0;ch<2;ch++){const a=output.getChannelData(ch);for(const x of a){if(!Number.isFinite(x))invalid++;sum+=x*x;peak=Math.max(peak,Math.abs(x));if(Math.abs(x)>=.94)above94++;}for(let i=0;i<a.length;i+=4800){let s=0;for(let j=i;j<Math.min(a.length,i+4800);j++)s+=a[j]*a[j];windows.push(Math.sqrt(s/Math.min(4800,a.length-i)));}}
                    const rms=Math.sqrt(sum/(output.length*2));return{rms,dbfs:20*Math.log10(Math.max(1e-12,rms)),peak,nearCeilingFraction:above94/(output.length*2),invalid,max100msRms:Math.max(...windows)};
                  }
                  const rows=[];
                  for(const key of Object.keys(range.manifest.audio))for(const distance of [20,160]){
                    const baseline=await render(key,distance,0,true),original=await render(key,distance,0),six=await render(key,distance,6),twelve=await render(key,distance,12);
                    rows.push({key,distance,baseline,original,six,twelve,gain6Db:six.dbfs-baseline.dbfs,gain12Db:twelve.dbfs-baseline.dbfs});
                  }
                  const dense=[];for(const db of [0,6,12])dense.push({db,...await render('tankShot',20,db,db===0,true)});
                  const low=[];for(const db of [0,6,12])low.push({db,...await render('tankMove',20,db,false,false,true)});
                  return{secondsPerRender:4,sampleRate:rate,masterVolume:1,rows,dense,low};
                }''')
                proof = result['waveforms']
                for row in proof['rows']:
                    assert abs(row['original']['rms']-row['baseline']['rms']) < 1e-7, row
                    for name in ['baseline', 'original', 'six', 'twelve']:
                        assert row[name]['peak'] < .951 and row[name]['invalid'] == 0, row
                    assert row['gain6Db'] > 1 and row['gain12Db'] > row['gain6Db']+.2, row
                for r in proof['dense']: assert r['peak'] < .951 and r['invalid'] == 0 and r['nearCeilingFraction'] < .05
                for db in [6, 12]: assert abs(proof['low'][db//6]['dbfs']-proof['low'][0]['dbfs']-db) < .02
                passed('13真实素材×近中两距离：改前与0dB等价，6／12dB平均输出确实提高，峰值受限')
                passed('低电平实测增益6／12dB；48路真实素材密集混音逐样本无非有限值')
                sources = value('Object.fromEntries([...range.mixer.engines].map(([id,e])=>[id,e.sound.uuid]))')
                for db in [12, 0, 6]:
                    page.locator('#enhancement').select_option(str(db)); wait('range.mixer.boostDb==='+str(db))
                after = value('Object.fromEntries([...range.mixer.engines].map(([id,e])=>[id,e.sound.uuid]))')
                assert sources == after
                page.locator('#bench60').click(); wait('!!range.benchmark.run')
                page.locator('#enhancement').select_option('12'); wait('!range.benchmark.run')
                assert '中止' in page.locator('#bench-status').inner_text()
                assert value('range.sim.shots') == 0
                passed('真实切档保持八个机械声源，不触发战场命令；档位改变中止采样')
                page.locator('#volume').fill('0'); page.locator('#volume').dispatch_event('input')
                result['zero_output_peak'] = page.evaluate('''async()=>{await new Promise(r=>setTimeout(r,500));const a=new Float32Array(1024);range.mixer.analyser.getFloatTimeDomainData(a);return Math.max(...a.map(Math.abs));}''')
                assert result['zero_output_peak'] < .000001
                page.locator('#volume').fill('100'); page.locator('#volume').dispatch_event('input')
                page.locator('#enhancement').select_option('6')
                page.locator('#pause').click(); wait('range.mixer.stats().voices===0 && range.mixer.anchors.size===0')
                page.locator('#pause').click(); wait('range.mixer.engines.size===8')
                page.locator('#sound').click(); wait('!range.mixer.active && range.mixer.stats().voices===0')
                page.locator('#sound').click(); wait('range.mixer.active && range.mixer.engines.size===8')
                page.locator('#reset').click(); wait('range.mixer.engines.size===8')
                assert value('range.mixer.boostDb') == 6 and value('range.audio.masterVolume') == 1
                passed('12dB档零音量真正静音；暂停／关闭清理，重置保留响度设置')
                page.set_viewport_size({'width': 1280, 'height': 800})
                assert value('document.documentElement.scrollWidth<=window.innerWidth')
                page.locator('#enhancement').scroll_into_view_if_needed()
                page.screenshot(path=str(OUT/(channel+'-响度控制.png')))
                passed('1280宽无页面横向溢出，响度控件可见')
            else:
                page.locator('#unit-count').select_option('100'); page.locator('#load-mode').select_option('combat')
                page.locator('#overview').click(); wait('range.view.span===range.sim.map.maxSpan && !range.view.centerTarget')
                for db in [6, 12]:
                    page.locator('#enhancement').select_option(str(db)); page.locator('#reset').click()
                    page.locator('#overview').click(); wait('range.view.span===range.sim.map.maxSpan && !range.view.centerTarget')
                    page.locator('#bench60').click(); wait('!!range.benchmark.run'); wait('!range.benchmark.run', 95000)
                    sample = value('range.benchmark.last'); sample['audio'] = value('range.mixer.stats()')
                    sample['anchors'] = value('({registered:range.mixer.anchors.size,inScene:range.view.scene.children.filter(o=>o.userData.expires!==undefined).length})')
                    assert sample['enhancementDb'] == db and sample['volume'] == 1 and sample['frames'] > 1000
                    assert sample['timeline'][-1]['shots'] > sample['timeline'][0]['shots']+20
                    assert all(p['voices'] <= 48 and p['engines'] <= 8 for p in sample['timeline'])
                    assert sample['audio']['peak'] < .951 and sample['audio']['engines'] > 0
                    assert sample['anchors']['registered'] == sample['anchors']['inScene']
                    result['samples'].append(sample)
                    passed('100单位'+str(db)+'dB有声交战60秒：P95 '+str(round(sample['p95'], 2))+'ms，预算与节点守恒')
            assert not result['errors'], result['errors']; result['passed'] = True
        except Exception as error:
            result['failure'] = str(error); save(); raise
        finally:
            save(); browser.close()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], default='chrome')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--performance', action='store_true')
    mode.add_argument('--lifecycle', action='store_true', help='复用既有七组真实声音交互，输出新阶段子目录')
    args = parser.parse_args()
    if args.lifecycle:
        import 验证载具音效 as lifecycle
        lifecycle.OUT = OUT/'生命周期'
        lifecycle.run(args.browser)
    else: run(args.browser, args.performance)
