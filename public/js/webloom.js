const PETAL_PATH = "M0,0 C-7,-6 -8,-16 -3,-22 C-1,-24 1,-24 3,-22 C8,-16 7,-6 0,0 Z";
     function rosePetal(radius, size, angle, color, opacity){
       return '<path d="'+PETAL_PATH+'" fill="'+color+'" opacity="'+opacity+'" transform="rotate('+angle+') translate(0,'+(-radius)+') scale('+size+')"/>';
     }
     function roseInner(s, colorA, colorB){
       let out = '';
       for(let i=0;i<6;i++) out += rosePetal(9*s, 0.85*s, 60*i+10, (colorB && i%2===1)?colorB:colorA, 0.82);
       for(let i=0;i<5;i++) out += rosePetal(5*s, 0.6*s, 72*i+32, colorA, 0.92);
       for(let i=0;i<3;i++) out += rosePetal(1.6*s, 0.38*s, 120*i+60, colorA, 1);
       out += '<circle cx="0" cy="0" r="'+(1.6*s)+'" fill="var(--gold)"/>';
       return out;
     }
 
     document.getElementById('today-rose-svg').innerHTML = '<g transform="translate(100,100)">'+roseInner(2.6, 'var(--amber)', 'var(--gold)')+'</g>';
 
     const REFLECTIONS = [
       "Today, you found calm in unexpected silence.",
       "You spent more time listening than speaking.",
       "Something small made today lighter.",
       "You stood still longer than you meant to.",
       "You stayed, without trying to fix anything.",
       "You laughed before you'd even had coffee.",
       "The morning stayed quiet, and so did you."
     ];
     let reflectIndex = 0;
     function cycleReflection(){
       const el = document.getElementById('reflect-text');
       el.classList.add('fading');
       setTimeout(()=>{
         reflectIndex = (reflectIndex+1) % REFLECTIONS.length;
         el.textContent = REFLECTIONS[reflectIndex];
         el.classList.remove('fading');
       }, 220);
     }
 
     (id){ document.getElementById(id).scrollIntoView({behavior:'smooth'}); }
 
     (function(){
       const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
       const hero = document.getElementById('hero');
       const heroBg = document.getElementById('hero-bg');
       const ringGlow = document.getElementById('ring-glow');
       const scrollCue = document.getElementById('scroll-cue');
       const navbar = document.getElementById('navbar');
       const progress = document.getElementById('scroll-progress');
 
       const revealEls = document.querySelectorAll('.reveal');
       const observer = new IntersectionObserver((entries)=>{
         entries.forEach(entry=>{
           if(entry.isIntersecting){
             entry.target.classList.add('visible');
             observer.unobserve(entry.target);
           }
         });
       }, {threshold:0.12, rootMargin:'0px 0px -40px 0px'});
       revealEls.forEach(el=>observer.observe(el));
 
       if(prefersReduced) return;
 
       let ticking = false;
 
       function update(){
         const scrollY = window.scrollY;
         const h = hero.offsetHeight;
         const p = Math.min(scrollY / h, 1);
 
         heroBg.style.transform = 'translateY('+(p * 50)+'px) scale('+(1 + p * 0.04)+')';
 
         const ringIntensity = Math.max(0, 1 - Math.abs(p - 0.15) * 6);
         ringGlow.style.opacity = String(Math.min(1, ringIntensity * 1.5));
         ringGlow.style.transform = 'translate(-50%,-50%) scale('+(1 + ringIntensity * 0.08)+')';
 
         scrollCue.style.opacity = String(Math.max(0, 1 - p * 3.5));
         if(p > 0.3) scrollCue.classList.add('hidden');
         else scrollCue.classList.remove('hidden');
 
         if(scrollY > h * 0.85) navbar.classList.add('scrolled');
         else navbar.classList.remove('scrolled');
 
         const docHeight = document.documentElement.scrollHeight - window.innerHeight;
         progress.style.width = (scrollY / docHeight * 100)+'%';
 
         ticking = false;
       }
 
       window.addEventListener('scroll', ()=>{
         if(!ticking){ requestAnimationFrame(update); ticking = true; }
       }, {passive:true});
 
       update();
     })();
   

// Webloom's rose/calendar/panel JS

const CATEGORY = {
  joy:{color:'var(--amber)', label:'Joy'},
  connection:{color:'var(--rose)', label:'Connection'},
  calm:{color:'var(--sage)', label:'Calm'},
  care:{color:'var(--lavender)', label:'Care'},
  wonder:{color:'var(--gold)', label:'Wonder'}
};

const monthDays = [
  {day:1, intensity:0},
  {day:2, intensity:2, category:'joy', reflection:"You laughed before you'd even had coffee.", context:'Morning · At home'},
  {day:3, intensity:1, category:'calm', reflection:'Today, you found calm in unexpected silence.', context:'Afternoon · Alone'},
  {day:4, intensity:0},
  {day:5, intensity:3, category:'connection', shared:true, reflection:'You both went quiet at the same time, and neither of you reached for a phone.', context:'Evening · Together'},
  {day:6, intensity:0},
  {day:7, intensity:0},
  {day:8, intensity:2, category:'care', reflection:'You called back, even though you were tired.', context:'Evening · Phone call'},
  {day:9, intensity:0},
  {day:10, intensity:2, category:'wonder', reflection:'You stood still longer than you meant to.', context:'Outdoors · Alone'},
  {day:11, intensity:0},
  {day:12, intensity:2, category:'connection', shared:true, reflection:'You spent more time listening than speaking.', context:'Evening · Together'},
  {day:13, intensity:0},
  {day:14, intensity:0},
  {day:15, intensity:1, category:'joy', reflection:'Something small made today lighter.', context:'Midday'},
  {day:16, intensity:0},
  {day:17, intensity:2, category:'calm', reflection:'The morning stayed quiet, and so did you.', context:'Morning · Outdoors'},
  {day:18, intensity:0},
  {day:19, intensity:0},
  {day:20, intensity:3, category:'connection', shared:true, reflection:'Ninety minutes passed and neither of you noticed.', context:'Afternoon · Together'},
  {day:21, intensity:0},
  {day:22, intensity:0},
  {day:23, intensity:1, category:'wonder', reflection:'A song stopped you mid-step.', context:'Commute'},
  {day:24, intensity:0},
  {day:25, intensity:2, category:'care', reflection:'You stayed, without trying to fix anything.', context:'Evening · With a friend'},
  {day:26, intensity:0},
  {day:27, intensity:3, category:'joy', shared:true, reflection:'The conversation ran long, and neither of you minded.', context:'Evening · Together'},
  {day:28, intensity:0}
];

const REFLECTIONS = [
  "Today, you found calm in unexpected silence.",
  "You spent more time listening than speaking.",
  "Something small made today lighter.",
  "You stood still longer than you meant to.",
  "You stayed, without trying to fix anything."
];
let reflectIndex = 0;

/* ---------- Rose generator ---------- */
const PETAL_PATH = "M0,0 C-7,-6 -8,-16 -3,-22 C-1,-24 1,-24 3,-22 C8,-16 7,-6 0,0 Z";

function rosePetal(radius, size, angle, color, opacity){
  return `<path d="${PETAL_PATH}" fill="${color}" opacity="${opacity}" transform="rotate(${angle}) translate(0,${-radius}) scale(${size})"/>`;
}

function roseInner(s, colorA, colorB){
  let out = '';
  const outerCount = 6;
  for(let i=0;i<outerCount;i++){
    const angle = (360/outerCount)*i + 10;
    const c = (colorB && i%2===1) ? colorB : colorA;
    out += rosePetal(9*s, 0.85*s, angle, c, 0.82);
  }
  const midCount = 5;
  for(let i=0;i<midCount;i++){
    const angle = (360/midCount)*i + 32;
    out += rosePetal(5*s, 0.6*s, angle, colorA, 0.92);
  }
  const innerCount = 3;
  for(let i=0;i<innerCount;i++){
    const angle = (360/innerCount)*i + 60;
    out += rosePetal(1.6*s, 0.38*s, angle, colorA, 1);
  }
  out += `<circle cx="0" cy="0" r="${1.6*s}" fill="var(--gold)"/>`;
  return out;
}

function roseGroup(dayNum, cx, cy, s, colorA, colorB, label){
  return `<g class="bloom" data-day="${dayNum}" tabindex="0" role="button" aria-label="${label}" transform="translate(${cx},${cy})">
    ${roseInner(s, colorA, colorB)}
  </g>`;
}

function quietMark(cx, cy){
  return `<g aria-hidden="true">
    <line x1="${cx}" y1="${cy+7}" x2="${cx}" y2="${cy-3}" stroke="var(--paper-dim)" stroke-width="1.2" opacity="0.5"/>
    <circle cx="${cx}" cy="${cy-6}" r="1.8" fill="var(--paper-dim)" opacity="0.5"/>
  </g>`;
}

function scaleForIntensity(i, base){ return (i===1?0.72:i===2?0.86:1.0)*base; }

/* ---------- Render: Today's Bloom ---------- */
function renderTodayBloom(){
  const bloomed = monthDays.filter(d=>d.intensity>0);
  const today = bloomed[bloomed.length-1];
  const cat = CATEGORY[today.category];
  const svg = document.getElementById('today-rose-svg');
  svg.innerHTML = `<g transform="translate(100,100)">${roseInner(2.6, cat.color, today.shared?'var(--gold)':null)}</g>`;
  document.getElementById('today-date').textContent = 'JUN ' + String(today.day).padStart(2,'0');
  document.getElementById('today-reflection').textContent = today.reflection;
  document.getElementById('today-shared').style.display = today.shared ? 'flex' : 'none';
  document.getElementById('today-tags').innerHTML = today.context.split(' · ').map(t=>`<span class="p-tag">${t}</span>`).join('');
}

/* ---------- Render: Calendar ---------- */
function renderCalendar(){
  const grid = document.getElementById('calendar-grid');
  grid.innerHTML = '';
  monthDays.forEach(d=>{
    if(d.intensity>0){
      const cat = CATEGORY[d.category];
      const s = scaleForIntensity(d.intensity, 0.62);
      const inner = `<g transform="translate(30,32)">${roseInner(s, cat.color, d.shared?'var(--gold)':null)}</g>`;
      grid.insertAdjacentHTML('beforeend',
        `<button class="cal-cell bloom" data-day="${d.day}" aria-label="Day ${d.day}: ${d.reflection}">
          <span class="cal-daynum">${d.day}</span><svg viewBox="0 0 60 60">${inner}</svg>
        </button>`);
    } else {
      grid.insertAdjacentHTML('beforeend',
        `<div class="cal-cell quiet" aria-hidden="true">
          <span class="cal-daynum">${d.day}</span>
          <svg viewBox="0 0 60 60">${quietMark(30,32)}</svg>
        </div>`);
    }
  });
}

function renderMonthlyGarden(){
  const svg = document.getElementById('monthly-svg');
  const bloomed = monthDays.filter(d=>d.intensity>0);
  const spacing = 900/(bloomed.length+1);
  const yJitter = [90,112,80,100,122,86,106,96,116,88,104,94];
  let out = '';
  bloomed.forEach((d,i)=>{
    const cat = CATEGORY[d.category];
    const cx = spacing*(i+1);
    const cy = yJitter[i % yJitter.length];
    const s = scaleForIntensity(d.intensity, 0.58);
    out += roseGroup(d.day, cx, cy, s, cat.color, d.shared?'var(--gold)':null, `Day ${d.day}: ${d.reflection}`);
  });
  svg.innerHTML = out;
}

function renderSynbloom(){
  const svg = document.getElementById('synbloom-svg');
  const sharedDays = monthDays.filter(d=>d.shared);
  const xs = [140,350,560];
  let out = '';
  sharedDays.forEach((d,i)=>{
    const cat = CATEGORY[d.category];
    out += roseGroup(d.day, xs[i], 100, 1.4, cat.color, 'var(--gold)', `Shared moment, day ${d.day}: ${d.reflection}`);
    out += `<text x="${xs[i]}" y="180" text-anchor="middle" class="synbloom-svg-caption">YOU &amp; MIA · JUN ${String(d.day).padStart(2,'0')}</text>`;
  });
  svg.innerHTML = out;
}

let lastFocused = null;
function openDayPanel(dayNum){
  const d = monthDays.find(x=>x.day===Number(dayNum));
  if(!d || d.intensity===0) return;
  const cat = CATEGORY[d.category];
  document.getElementById('panel-dot').style.background = cat.color;
  document.getElementById('panel-cat-label').textContent = cat.label;
  document.getElementById('panel-date').textContent = 'JUN ' + String(d.day).padStart(2,'0');
  document.getElementById('panel-title').textContent = d.reflection;
  const sharedRow = document.getElementById('panel-shared');
  if(d.shared){ sharedRow.style.display = 'flex'; document.getElementById('panel-shared-text').textContent = 'Shared with Mia'; }
  else { sharedRow.style.display = 'none'; }
  document.getElementById('panel-tags').innerHTML = d.context.split(' · ').map(t=>`<span class="p-tag">${t}</span>`).join('');
  document.getElementById('panel').classList.add('open');
  document.getElementById('panel').setAttribute('aria-hidden','false');
  document.getElementById('scrim').classList.add('open');
  document.getElementById('panel-close').focus();
}
function closePanel(){
  document.getElementById('panel').classList.remove('open');
  document.getElementById('panel').setAttribute('aria-hidden','true');
  document.getElementById('scrim').classList.remove('open');
  if(lastFocused) lastFocused.focus();
}

document.addEventListener('click', e=>{
  const el = e.target.closest('.bloom');
  if(el && el.dataset.day){ lastFocused = el; openDayPanel(el.dataset.day); }
});
document.addEventListener('keydown', e=>{
  if(e.key==='Escape'){ closePanel(); return; }
  if(e.key==='Enter' || e.key===' '){
    const el = e.target.closest('.bloom');
    if(el && el.dataset.day && el.tagName!=='BUTTON'){ e.preventDefault(); lastFocused = el; openDayPanel(el.dataset.day); }
  }
});

function scrollToId(id){ document.getElementById(id).scrollIntoView({behavior:'smooth'}); }); }

/* ---------- InBloom: signal → growth demo ---------- */
document.getElementById('grow-rose').innerHTML = roseInner(1.3, 'var(--amber)', null);

function playGrowth(){
  const stem = document.getElementById('grow-stem');
  const leaves = document.getElementById('grow-leaves');
  const rose = document.getElementById('grow-rose');
  const caption = document.getElementById('grow-caption');
  stem.classList.remove('grown'); leaves.classList.remove('shown');
  rose.classList.remove('bloomed'); caption.classList.remove('shown');
  void stem.getBoundingClientRect();
  requestAnimationFrame(()=>{
    stem.classList.add('grown');
    setTimeout(()=>leaves.classList.add('shown'), 450);
    setTimeout(()=>{ rose.classList.add('bloomed'); caption.classList.add('shown'); }, 1000);
  });
}

function cycleReflection(){
  const el = document.getElementById('reflect-text');
  el.classList.add('fading');
  setTimeout(()=>{
    reflectIndex = (reflectIndex+1) % REFLECTIONS.length;
    el.textContent = REFLECTIONS[reflectIndex];
    el.classList.remove('fading');
  }, 220);
}

renderTodayBloom();
renderCalendar();
renderMonthlyGarden();
renderSynbloom();

/* ---------- Image hero: scroll parallax + fade ---------- */
(function(){
  const heroImage = document.getElementById('hero-image');
  const heroContent = document.querySelector('.hero-image-content');
  const heroBg = document.querySelector('.hero-image-bg');
  const scrollCue = document.querySelector('.scroll-cue');
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(!heroImage || prefersReduced) return;
  let ticking = false;
  function update(){
    const h = heroImage.offsetHeight || 1;
    const p = Math.min(Math.max(window.scrollY / h, 0), 1);
    heroContent.style.opacity = String(Math.max(0, 1 - p*1.3));
    heroContent.style.transform = `translateY(${p*-40}px)`;
    heroBg.style.transform = `translateY(${p*7}%) scale(${1 + p*0.06})`;
    scrollCue.style.opacity = String(Math.max(0, 0.7 - p*3));
    ticking = false;
  }
  window.addEventListener('scroll', ()=>{
    if(!ticking){ requestAnimationFrame(update); ticking = true; }
  }, {passive:true});
  update();
})();
