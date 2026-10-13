(()=>{const RM=matchMedia('(prefers-reduced-motion: reduce)').matches,$=id=>document.getElementById(id);
const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}}),{threshold:.15});
document.querySelectorAll('.rv').forEach(el=>RM?el.classList.add('in'):io.observe(el));
const w=$('wave');let t=0;function wave(){let d='M0 30';for(let x=0;x<=400;x+=8){const y=30+Math.sin(x/18+t)*10*Math.sin(x/90+t/3)+(Math.sin(x*1.7+t*5)>0.93?-18:0);d+=` L${x} ${y.toFixed(1)}`}w.setAttribute('d',d)}
function stampTime(){$('stampt').textContent=new Date().toISOString().replace('T',' · ').slice(0,21)+'Z'}
if(RM){wave();$('sb').classList.add('locked');$('lockt').textContent='LOCKED · final 21–17';$('lbf').style.width='100%';stampTime();$('stamp').classList.add('on');return}
(function loop(){t+=.05;wave();requestAnimationFrame(loop)})();
const sigs=[...document.querySelectorAll('.sg')];
async function cycle(){const sl=ms=>new Promise(r=>setTimeout(r,ms));
 $('stamp').classList.remove('on');$('sb').classList.remove('locked');sigs.forEach(s=>s.classList.remove('on'));
 let s=12;$('sh').textContent='14';$('lockt').textContent='reading scorebug…';
 for(let i=0;i<=12;i++){$('sc').textContent='0:'+String(s-i).padStart(2,'0');$('lbf').style.width=(i/12*100)+'%';if(i==6)$('sh').textContent='21';await sl(180)}
 $('sq').textContent='FINAL';$('sb').classList.add('locked');$('lockt').textContent='LOCKED · 3 agreeing reads · referee co-signing';
 await sl(700);stampTime();$('stamp').classList.add('on');
 for(const g of sigs){await sl(450);g.classList.add('on')}
 await sl(3200);$('sq').textContent='Q4';cycle()}
cycle();
const keys=[...document.querySelectorAll('.pad span')];let k=0;setInterval(()=>{keys.forEach(e=>e.classList.remove('hit'));keys[k%keys.length].classList.add('hit');k++},520);
})();
