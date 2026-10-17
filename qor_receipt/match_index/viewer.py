from __future__ import annotations

import base64
import copy
import html
import json
from pathlib import Path


def _embed(out: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    p = out / rel
    if not p.exists():
        return None
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def write_viewer(idx: dict, out: Path) -> Path:
    data = copy.deepcopy(idx)
    for e in data["events"]:
        for f in e.get("frames") or []:
            f["src"] = _embed(out, f["file"])
        e["contact_src"] = _embed(out, e.get("contact_sheet"))
    blob = json.dumps(data).replace("</", "<\\/")
    title = html.escape(f"QorTime · what the AI saw · {idx.get('match_id') or 'match'}")
    p = out / "viewer.html"
    p.write_text(TEMPLATE.replace("__TITLE__", title).replace("__DATA__", blob), encoding="utf-8")
    return p


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>__TITLE__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#060b16;--panel:#0b1426;--line:#16233d;--ink:#dbe7f5;--mute:#7d8ba3;--aqua:#3fe0d0;--aqua2:#1aa89b;--gold:#e8c35a;--warn:#f08a5d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;align-items:center;gap:18px;padding:14px 22px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#0a1324,#060b16)}
.brand{font-weight:700;letter-spacing:.14em;color:var(--aqua);font-size:12px}.mid{color:var(--mute);font-size:12px}
.final{margin-left:auto;display:flex;gap:10px;align-items:center}.score{font:700 22px ui-monospace,Menlo,monospace;padding:4px 12px;border-radius:8px;border:1px solid var(--line)}
.locked{color:var(--gold);border-color:var(--gold);box-shadow:0 0 14px #e8c35a33}.unl{color:var(--aqua)}
.tag{font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:2px 7px;border-radius:99px;border:1px solid var(--line);color:var(--mute)}
.tag.g{color:var(--gold);border-color:#e8c35a88}.tag.a{color:var(--aqua);border-color:#3fe0d066}.tag.w{color:var(--warn);border-color:#f08a5d66}
#tl{position:relative;height:66px;margin:14px 22px 6px;border:1px solid var(--line);border-radius:10px;background:var(--panel)}
#tl .bar{position:absolute;left:14px;right:14px;top:32px;height:2px;background:var(--line)}
.dot{position:absolute;top:24px;width:18px;height:18px;margin-left:-9px;border-radius:50%;border:2px solid var(--aqua);background:var(--bg);cursor:pointer}
.dot.lk{border-color:var(--gold);background:#e8c35a33}.dot.sel{box-shadow:0 0 0 4px #3fe0d044}.dot .lb{position:absolute;top:-19px;left:50%;transform:translateX(-50%);white-space:nowrap;font-size:10px;color:var(--mute)}
#chips{display:flex;gap:6px;flex-wrap:wrap;margin:0 22px 10px}.chip{padding:4px 9px;border:1px solid var(--line);border-radius:7px;cursor:pointer;color:var(--mute);background:var(--panel)}.chip.sel{color:var(--ink);border-color:var(--aqua2)}
main{display:grid;grid-template-columns:1fr 360px;gap:14px;padding:0 22px 22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.card h3{margin:0 0 8px;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--mute);font-weight:600}
#frames{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}#frames figure{margin:0}#frames img{width:100%;display:block;border-radius:6px;border:1px solid var(--line)}
#frames figcaption{font:10px ui-monospace,monospace;color:var(--mute);text-align:center}.zero img{border-color:var(--aqua)!important}
.miss{color:var(--warn);font-style:italic}#frames>.miss{grid-column:1/-1}.kv{display:grid;grid-template-columns:120px 1fr;gap:3px 10px;font-size:12px}.kv b{color:var(--mute);font-weight:500}
.mono{font:11px ui-monospace,Menlo,monospace;word-break:break-all}.adv{font-size:11px;color:var(--mute);border-left:2px solid var(--aqua2);padding-left:8px;margin:6px 0}
.big{font:700 28px ui-monospace,monospace}svg text{fill:var(--mute);font:10px ui-monospace,monospace}
</style></head><body>
<header><div><div class="brand">QORTIME · WHAT THE AI SAW</div><div class="mid" id="mid"></div></div><div class="final" id="final"></div></header>
<div id="tl"><div class="bar"></div></div><div id="chips"></div>
<main><div><div class="card"><h3 id="fh">Burst frames</h3><div id="frames"></div></div>
<div class="card" style="margin-top:14px"><h3>Inputs &amp; haptics around the moment <span class="tag a">advisory</span></h3><div id="strip"></div><div class="adv" id="adv"></div></div></div>
<div><div class="card"><h3>Score read</h3><div id="read"></div></div><div class="card" style="margin-top:14px"><h3>Chain binding</h3><div id="chain"></div></div></div></main>
<script>
const D=__DATA__;const $=s=>document.querySelector(s);const M='<span class="miss">missing</span>';
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const v=x=>(x===null||x===undefined)?M:esc(x);const sc=s=>s?`${s.away} – ${s.home}`:null;
const clk=s=>s==null?null:`${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
const R=D.result||{},rr=R.receipt_result||{};
$('#mid').innerHTML=`${v(D.match_id)} · role ${v(D.role)} · qorcap ${v(D.qorcap_version)} · ${D.events.length} events · video ${v(D.video.file)} (${v(D.video.alignment)})`;
const fs=R.final?sc(R.final):null;
$('#final').innerHTML=fs&&R.final_confirmed?`<span class="tag g">final confirmed</span><span class="score locked">${esc(fs)}</span>`:
 `<span class="tag w">final not confirmed</span><span class="mid">last scorebug read</span><span class="score unl">${rr.away_score!=null?esc(rr.away_score+' – '+rr.home_score):M}</span><span class="mid">away – home</span>`;
const ts=D.events.map(e=>e.t_ms),t0=Math.min(...ts),t1=Math.max(...ts);const tl=$('#tl');
D.events.forEach((e,i)=>{const d=document.createElement('div');d.className='dot'+(e.locked?' lk':'');
 d.style.left=`calc(14px + (100% - 28px)*${t1>t0?(e.t_ms-t0)/(t1-t0):0})`;d.title=e.id;d.innerHTML=`<span class="lb">${i%2?'':esc(lab(e))}</span>`;d.onclick=()=>show(i);tl.appendChild(d);
 const c=document.createElement('div');c.className='chip';c.textContent=lab(e);c.onclick=()=>show(i);$('#chips').appendChild(c);});
function lab(e){return e.kind==='score_change'?sc(e.score_after):e.kind==='quarter_change'?'Q'+e.quarter:e.kind.replace(/_/g,' ')}
function strip(s){if(!s)return `<div>${M} (no inputs/haptics in bundle)</div>`;const W=700,H=120,n=s.presses.length,bw=W/n;
 const mp=Math.max(1,...s.presses),mh=Math.max(1,...s.haptic_energy);let g='';
 for(let i=0;i<n;i++){const x=i*bw;const ph=s.presses[i]/mp*48,hh=s.haptic_energy[i]/mh*48;
  g+=`<rect x="${x+1}" y="${54-ph}" width="${bw-2}" height="${ph}" fill="#3fe0d0"/><rect x="${x+1}" y="62" width="${bw-2}" height="${hh}" fill="#7d8ba3"/>`}
 const zx=(-s.t0_s)/s.bin_s*bw;g+=`<line x1="${zx}" x2="${zx}" y1="0" y2="${H}" stroke="#e8c35a" stroke-dasharray="3 3"/>`;
 for(let t=Math.ceil(s.t0_s);t<=s.t0_s+n*s.bin_s;t+=5){const x=(t-s.t0_s)/s.bin_s*bw;g+=`<text x="${x+2}" y="${H-2}">${t>0?'+':''}${t}s</text>`}
 return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}<text x="4" y="10">button presses</text><text x="4" y="74">haptic energy</text></svg>`}
function cp(c,l){return c?`<div class="kv"><b>${l}</b><span>seq ${v(c.seq)} · ${v(c.trigger)}</span><b>hash</b><span class="mono">${v(c.hash)}</span><b>frames_hash</b><span class="mono">${v(c.frames_hash)} ${c.frames_hash_matches_evidence===true?'<span class="tag a">matches evidence</span>':c.frames_hash_matches_evidence===false?'<span class="tag w">MISMATCH</span>':''}</span><b>segment</b><span class="mono">#${v(c.segment_seq)} ${v(c.segment_hash)}</span></div>`:`<div class="kv"><b>${l}</b>${M}</div>`}
function show(i){const e=D.events[i];document.querySelectorAll('.dot').forEach((d,j)=>d.classList.toggle('sel',j===i));document.querySelectorAll('.chip').forEach((d,j)=>d.classList.toggle('sel',j===i));
 $('#fh').innerHTML=`Burst frames · ${esc(e.id)} ${e.cuts_video_s&&e.cuts_video_s.length?`<span class="tag">${e.cuts_video_s.length} cuts</span>`:''}`;
 $('#frames').innerHTML=e.frames&&e.frames.length?e.frames.map(f=>`<figure class="${Math.abs(f.dt_s)<0.3?'zero':''}">${f.src?`<img src="${f.src}">`:`<div class="miss">frame missing</div>`}<figcaption>${f.dt_s>=0?'+':''}${f.dt_s.toFixed(1)}s</figcaption></figure>`).join(''):`<div class="miss">No frames: ${esc(e.frames_missing_reason||'missing')}</div>`;
 const after=sc(e.score_after);const gold=e.locked;
 $('#read').innerHTML=`<div class="big" style="color:${gold?'var(--gold)':'var(--aqua)'}">${after?esc(after):M}</div><div class="mid">away – home ${gold?'<span class="tag g">locked</span>':'<span class="tag">read, not locked</span>'}</div>
 <div class="kv" style="margin-top:8px"><b>kind</b><span>${esc(e.kind)}</span><b>before</b><span>${v(sc(e.score_before))}</span><b>quarter / clock</b><span>Q${v(e.quarter)} · ${v(clk(e.clock_s))}</span>
 <b>wall time</b><span>${new Date(e.t_ms).toISOString()}</span><b>recording</b><span>${v(e.recording_s)} s</span><b>confidence</b><span>${v(e.confidence)} <span class="mid">${esc(e.confidence_note)}</span></span><b>basis</b><span>${esc(e.basis)}</span>
 ${e.kind==='end_of_game'?`<b>final confirmed</b><span>${e.final_confirmed?'yes':'<span class="miss">no</span>'}</span><b>method</b><span>${v(e.final_method)}</span>`:''}${e.legal_step===false?'<b>check</b><span class="tag w">not a legal scoring step</span>':''}</div>`;
 const c=e.checkpoints||{};$('#chain').innerHTML=cp(c.trigger_match,'trigger cp')+cp(c.before,'cp before')+cp(c.after,'cp after')+
 `<div class="kv"><b>clip</b>${c.clip?`<span class="mono">${esc(c.clip.name)} ${esc(c.clip.sha256)}</span>`:M}</div><div class="kv"><b>chain head</b><span class="mono">${v(D.chain.chain_head)}</span></div>`;
 const I=e.inputs;$('#strip').innerHTML=strip(I&&I.strip);
 $('#adv').innerHTML=`${esc(D.input_summary.advisory)}<br>${I?`${I.presses.length} presses, ${I.haptics.length} haptic events in −5s..+3s. Bundle label: ${v(D.input_summary.human_label)} (${v(D.input_summary.human_permille)}‰, advisory).`:M}`}
{const h=e=>e.frames&&e.frames.length;let k=D.events.findIndex(e=>e.kind==='score_change'&&h(e));if(k<0)k=D.events.findIndex(h);show(Math.max(0,k));}
</script></body></html>"""
