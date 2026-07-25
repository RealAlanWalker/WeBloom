    // ── Shared utilities ──
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

    function scrollToId(id){ document.getElementById(id).scrollIntoView({behavior:'smooth'}); }

    // ── Mock data for Bloom Calendar ──
    const FRIENDS = [
      { name:'Mia',    avatar:'🌸', color:'var(--rose)' },
      { name:'Leo',    avatar:'🌿', color:'var(--sage)' },
      { name:'Sara',   avatar:'🌼', color:'var(--amber)' },
      { name:'James',  avatar:'🍂', color:'var(--gold)' },
      { name:'Ava',    avatar:'🌺', color:'var(--lavender)' },
      { name:'Noah',   avatar:'🌲', color:'var(--paper-dim)' },
      { name:'Emma',   avatar:'🪷', color:'var(--rose)' },
      { name:'Oliver', avatar:'🌾', color:'var(--amber)' },
    ];

    const MOODS = [
      { emoji:'😊', label:'Joyful — light and easy' },
      { emoji:'😌', label:'Peaceful — comfortable silence' },
      { emoji:'🤗', label:'Warm — felt truly seen' },
      { emoji:'💭', label:'Thoughtful — a lingering conversation' },
      { emoji:'😄', label:'Playful — laughter came easily' },
      { emoji:'🥰', label:'Connected — deep presence' },
      { emoji:'🫂', label:'Tender — a needed embrace' },
      { emoji:'✨', label:'Inspired — ideas sparked between us' },
    ];

    const CONVOS = [
      "「You know, I've been thinking about what you said last week — about letting things be. I tried it. It was harder than I expected, but also… quieter.」",
      "「Remember that café we used to go to? I walked past it today. Same smell of burnt espresso. Same wobbly table by the window. I sat there for an hour.」",
      "「I don't think I've ever told anyone that before. It just… came out. Thank you for not trying to fix it.」",
      "「She said the funniest thing today — completely out of nowhere — and I immediately thought, I have to tell you this.」",
      "「We just walked. Didn't say much. But I felt better after. Like something I'd been carrying got a little lighter.」",
      "「You looked different today. Not in a bad way — just… more yourself. Whatever you're doing, keep doing it.」",
      "「I read that book you mentioned. The one about gardens and grief. I cried on page 40. You were right about it.」",
      "「Can we make this a thing? Just… sitting here, no phones, no agenda. I didn't know how much I needed this.」",
      "「You're one of the few people I can be quiet with and not feel like I'm failing at conversation.」",
      "「Today was hard. But seeing you made it less hard. That's all I wanted to say.」",
    ];

    // Deterministic pseudo-random seeded by date string
    function daySeed(dateStr){
      let h = 0;
      for(let i=0;i<dateStr.length;i++){ h = ((h<<5)-h)+dateStr.charCodeAt(i); h |= 0; }
      return Math.abs(h);
    }

    // Generate data for one day
    function dayData(dateStr){
      const s = daySeed(dateStr);
      const dow = new Date(dateStr).getDay(); // 0=Sun
      const isWeekend = dow === 0 || dow === 6;

      // Bloom level 0-4, weighted toward mid-range, weekends slightly higher
      let level;
      const r = s % 100;
      if(r < 8) level = 0;
      else if(r < 25) level = 1;
      else if(r < 60) level = 2;
      else if(r < 85) level = 3;
      else level = 4;
      if(isWeekend && level < 2 && (s%3===0)) level = Math.min(4, level + 1 + (s%2));

      // Physiological data — varied but realistic ranges
      const physio = {
        heartRate:    55 + (s % 45),                          // 55–99 bpm
        hrv:          22 + (s % 58),                          // 22–79 ms
        steps:        1800 + (s % 13200),                     // 1,800–15,000
        sleepQuality: 58 + (s % 37),                          // 58–94 %
        stressLevel:  10 + (s % 65),                          // 10–74 %
        skinTemp:     (33.1 + (s % 40) / 10).toFixed(1),      // 33.1–37.0 °C
      };

      // Encounters — some days have friend meetings
      const encounters = [];
      const encounterChance = isWeekend ? 0.55 : 0.22;
      const numEncounters = (s % 100) < (encounterChance * 100) ? 1 + ((s>>3) % (isWeekend ? 3 : 2)) : 0;

      const usedFriends = new Set();
      for(let i=0;i<numEncounters;i++){
        let fi = (s + i*7 + i*i*13) % FRIENDS.length;
        // avoid duplicates by shifting
        let tries = 0;
        while(usedFriends.has(fi) && tries < FRIENDS.length){
          fi = (fi+1) % FRIENDS.length;
          tries++;
        }
        usedFriends.add(fi);
        const friend = FRIENDS[fi];
        const mood = MOODS[(s + i*11) % MOODS.length];
        const convo = CONVOS[(s + i*17 + i*i) % CONVOS.length];
        const hour = 9 + ((s + i*19) % 13); // 9am–9pm
        const min = ((s + i*23) % 4) * 15;  // 0, 15, 30, 45
        encounters.push({
          friend: friend,
          mood: mood,
          convo: convo,
          time: String(hour).padStart(2,'0')+':'+String(min).padStart(2,'0'),
        });
      }

      return { date: dateStr, level, physio, encounters, isWeekend };
    }

    // ── Build the calendar heatmap ──
    const CAL_START = new Date(2025, 0, 1);  // January 1, 2025
    const CAL_END   = new Date(2026, 11, 31); // December 31, 2026
    const TODAY_STR = '2026-07-25';            // today — dates after this are "future"

    function dateStr(d){ return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }

    // Collect all dates in range
    const allDates = [];
    const cursor = new Date(CAL_START);
    while(cursor <= CAL_END){
      allDates.push(dateStr(cursor));
      cursor.setDate(cursor.getDate() + 1);
    }

    // Group by week (Sun–Sat)
    const weeks = [];
    let currentWeek = [];
    // Pad start so first column is a Sunday
    const firstDow = CAL_START.getDay();
    for(let i=0;i<firstDow;i++) currentWeek.push(null);

    for(const d of allDates){
      const dow = new Date(d).getDay();
      if(dow === 0 && currentWeek.length > 0){
        weeks.push(currentWeek);
        currentWeek = [];
      }
      currentWeek.push(d);
      if(dow === 6){
        weeks.push(currentWeek);
        currentWeek = [];
      }
    }
    if(currentWeek.length > 0){
      while(currentWeek.length < 7) currentWeek.push(null);
      weeks.push(currentWeek);
    }

    // Pre-generate all day data
    const allDayData = {};
    for(const d of allDates){ allDayData[d] = dayData(d); }

    // Month labels — position over the first week column that contains days of that month
    const monthNames = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
    const monthSpans = []; // { label, colStart, colSpan }
    let prevMonth = -1;
    for(let ci=0;ci<weeks.length;ci++){
      const firstNonNull = weeks[ci].find(d=>d!==null);
      if(!firstNonNull) continue;
      const m = new Date(firstNonNull).getMonth();
      if(m !== prevMonth){
        if(monthSpans.length > 0) monthSpans[monthSpans.length-1].colSpan = ci - monthSpans[monthSpans.length-1].colStart;
        monthSpans.push({ label: monthNames[m], colStart: ci, colSpan: 1 });
        prevMonth = m;
      }
    }
    if(monthSpans.length > 0) monthSpans[monthSpans.length-1].colSpan = weeks.length - monthSpans[monthSpans.length-1].colStart;

    // Render
    const dayLabels = ['','Mon','','Wed','','Fri',''];
    const root = document.getElementById('calendar-root');

    // Month row
    let monthsHTML = '<div class="calendar-months">';
    // Spacer to align with first week column
    monthsHTML += '<span style="width:114px;flex-shrink:0;position:sticky;left:0;background:var(--ink-soft);z-index:5;"></span>';
    for(let ci=0;ci<weeks.length;ci++){
      const ms = monthSpans.find(m=>m.colStart===ci);
      if(ms){
        monthsHTML += '<span style="width:'+(ms.colSpan*51 - 9)+'px;flex-shrink:0;">'+ms.label+'</span>';
      }
    }
    monthsHTML += '</div>';

    // Grid body
    let bodyHTML = '<div class="calendar-body">';
    // Day-of-week labels
    bodyHTML += '<div class="calendar-day-labels">';
    for(let r=0;r<7;r++) bodyHTML += '<span>'+dayLabels[r]+'</span>';
    bodyHTML += '</div>';
    // Week columns
    bodyHTML += '<div class="calendar-weeks">';
    for(const week of weeks){
      bodyHTML += '<div class="calendar-week">';
      for(let r=0;r<7;r++){
        const d = week[r];
        if(d === null || d === undefined){
          bodyHTML += '<div class="day-cell empty" aria-hidden="true"></div>';
        } else {
          const data = allDayData[d];
          const tipText = d+' — Level '+data.level+(data.encounters.length>0?' · '+data.encounters.length+' friend'+(data.encounters.length>1?'s':''):'');
          const encClass = data.encounters.length > 0 ? ' has-encounter' : '';
          const futureClass = d > TODAY_STR ? ' future' : '';
          bodyHTML += '<div class="day-cell'+encClass+futureClass+'" data-date="'+d+'" data-level="'+data.level+'" data-encounters="'+data.encounters.length+'" tabindex="0" role="button" aria-label="'+tipText+'"><span class="day-cell-tip">'+tipText+'</span></div>';
        }
      }
      bodyHTML += '</div>';
    }
    bodyHTML += '</div></div>';

    root.innerHTML = monthsHTML + bodyHTML;

    // ── Day click → show detail panel ──
    let selectedDate = null;
    const detailPanel = document.getElementById('day-detail');

    function showDayDetail(dateStr){
      selectedDate = dateStr;
      const data = allDayData[dateStr];
      if(!data) return;

      // Highlight selected cell
      document.querySelectorAll('.day-cell.selected').forEach(el=>el.classList.remove('selected'));
      const cell = document.querySelector('.day-cell[data-date="'+dateStr+'"]');
      if(cell) cell.classList.add('selected');

      const d = new Date(dateStr);
      const weekdayNames = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
      const monthNamesFull = ['January','February','March','April','May','June','July','August','September','October','November','December'];
      const formattedDate = monthNamesFull[d.getMonth()]+' '+d.getDate()+', '+d.getFullYear();
      const weekday = weekdayNames[d.getDay()];

      const p = data.physio;
      let html = '';

      // Header
      html += '<div class="detail-header">';
      html += '<div><div class="detail-date">'+formattedDate+'</div><div class="detail-day">'+weekday+' · Bloom level '+data.level+'</div></div>';
      html += '<button class="detail-close" onclick="closeDayDetail()" aria-label="Close detail">Close ✕</button>';
      html += '</div>';

      // Physiological metrics
      html += '<p class="detail-section-title">Your Body That Day</p>';
      html += '<div class="physio-grid">';
      html += physioTile('Heart Rate', p.heartRate, 'bpm', p.heartRate<60?'Resting':p.heartRate<75?'Relaxed':p.heartRate<90?'Moderate':'Elevated');
      html += physioTile('HRV', p.hrv, 'ms', p.hrv>60?'High — well recovered':p.hrv>40?'Moderate':p.hrv>25?'Low — may be stressed':'Very low');
      html += physioTile('Steps', p.steps.toLocaleString(), '', p.steps>10000?'Very active':p.steps>7000?'Moderately active':p.steps>4000?'Light activity':'Restful day');
      html += physioTile('Sleep Quality', p.sleepQuality, '%', p.sleepQuality>85?'Deep restorative':p.sleepQuality>70?'Good':p.sleepQuality>55?'Fair':'Restless night');
      html += physioTile('Stress Level', p.stressLevel, '%', p.stressLevel<25?'Very low':p.stressLevel<45?'Low':p.stressLevel<60?'Moderate':'Elevated');
      html += physioTile('Skin Temp', p.skinTemp, '°C', parseFloat(p.skinTemp)>36.5?'Warm — active circulation':parseFloat(p.skinTemp)>35.5?'Normal':'Cool — at rest');
      html += '</div>';

      // Encounters
      html += '<p class="detail-section-title">Friends You Met</p>';
      if(data.encounters.length === 0){
        html += '<div class="no-encounters"><span class="quiet-icon">🌱</span>A quiet day. No friends recorded — and that belongs in the garden too.</div>';
      } else {
        html += '<div class="encounter-list">';
        for(const enc of data.encounters){
          html += '<div class="encounter-card">';
          html += '<div class="enc-avatar" style="background:'+enc.friend.color+'20;">'+enc.friend.avatar+'</div>';
          html += '<div>';
          html += '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0.3rem;">';
          html += '<span class="enc-name">'+enc.friend.name+'</span>';
          html += '<span class="enc-time">'+enc.time+'</span>';
          html += '</div>';
          html += '<div class="enc-mood">'+enc.mood.emoji+' '+enc.mood.label+'</div>';
          html += '<div class="enc-convo">'+enc.convo+'</div>';
          html += '</div>';
          html += '</div>';
        }
        html += '</div>';
      }

      detailPanel.innerHTML = html;
      detailPanel.classList.add('visible');
      detailPanel.scrollIntoView({behavior:'smooth', block:'nearest'});
    }

    function physioTile(label, value, unit, sub){
      return '<div class="physio-item"><div class="physio-label">'+label+'</div><span class="physio-value">'+value+'<span class="physio-unit">'+unit+'</span></span><div class="physio-sub">'+sub+'</div></div>';
    }

    function closeDayDetail(){
      selectedDate = null;
      document.querySelectorAll('.day-cell.selected').forEach(el=>el.classList.remove('selected'));
      detailPanel.classList.remove('visible');
      document.getElementById('calendar').scrollIntoView({behavior:'smooth', block:'start'});
    }

    // Click & keyboard handlers on day cells
    document.getElementById('calendar-root').addEventListener('click', function(e){
      const cell = e.target.closest('.day-cell');
      if(!cell || cell.classList.contains('empty')) return;
      showDayDetail(cell.dataset.date);
    });

    document.getElementById('calendar-root').addEventListener('keydown', function(e){
      if(e.key === 'Enter' || e.key === ' '){
        const cell = e.target.closest('.day-cell');
        if(!cell || cell.classList.contains('empty')) return;
        e.preventDefault();
        showDayDetail(cell.dataset.date);
      }
    });

    // ── Hero Carousel ──
    (function(){
      const slides = document.querySelectorAll('.hero-slide');
      const dots = document.querySelectorAll('.carousel-dot');
      const prevBtn = document.getElementById('carousel-prev');
      const nextBtn = document.getElementById('carousel-next');
      const heroContent = document.getElementById('hero-content');
      const heroEyebrow = document.getElementById('hero-eyebrow');
      const heroTitle = document.getElementById('hero-title');
      const heroTagline = document.getElementById('hero-tagline');
      let currentIndex = 0;
      let autoTimer = null;
      const AUTO_INTERVAL = 6000;

      // Slide content config
      const slideContent = [
        {
          eyebrow: 'A new kind of wearable',
          title: 'We<span class=\"highlight\">Bloom</span>',
          tagline: 'A ring, and the garden it grows.<br>Designed for presence, not distraction.',
        },
        {
          eyebrow: 'Inspired by The Little Prince',
          title: 'The <span class=\"highlight-rose\">Rose</span> you tend',
          tagline: 'What is essential is invisible to the eye.<br>Every moment of care makes a relationship unique.',
        },
        {
          eyebrow: 'A garden shared',
          title: 'Two rings, one <span class=\"highlight-sage\">Garden</span>',
          tagline: 'When rings meet, both gardens bloom.<br>Relationships are not found — they are grown together.',
        },
        {
          eyebrow: 'Forged in titanium. Finished by hand.',
          title: 'Designed to be <span class=\"highlight\">forgotten</span>',
          tagline: '3.2 grams. 2.5 millimeters thin.<br>You forget you are wearing it — until it reminds you what mattered.',
        },
        {
          eyebrow: 'Your day, reflected',
          title: 'Every evening, a <span class=\"highlight-rose\">BloomNote</span>',
          tagline: 'One sentence. Not what happened — but what it felt like.<br>A garden grows, petal by petal, day by day.',
        },
        {
          eyebrow: 'A ring, and the garden it grows',
          title: 'We<span class=\"highlight\">Bloom</span>',
          tagline: 'Designed for presence, not distraction.<br>Built to preserve meaning, not data.',
        },
      ];

      function goToSlide(index){
        if(index === currentIndex) return;
        // Fade out content
        heroContent.style.opacity = '0';
        heroContent.style.transform = 'translateY(10px)';
        heroContent.style.transition = 'opacity 0.35s ease, transform 0.35s ease';

        // Switch slides
        slides[currentIndex].classList.remove('active');
        dots[currentIndex].classList.remove('active');
        dots[currentIndex].setAttribute('aria-selected', 'false');

        currentIndex = index;

        slides[currentIndex].classList.add('active');
        dots[currentIndex].classList.add('active');
        dots[currentIndex].setAttribute('aria-selected', 'true');

        // Fade in new content after a short delay
        setTimeout(()=>{
          const c = slideContent[currentIndex];
          heroEyebrow.innerHTML = c.eyebrow;
          heroTitle.innerHTML = c.title;
          heroTagline.innerHTML = c.tagline;
          heroContent.style.opacity = '1';
          heroContent.style.transform = 'translateY(0)';
        }, 300);

        resetAutoAdvance();
      }

      function nextSlide(){ goToSlide((currentIndex + 1) % slides.length); }
      function prevSlide(){ goToSlide((currentIndex - 1 + slides.length) % slides.length); }

      prevBtn.addEventListener('click', prevSlide);
      nextBtn.addEventListener('click', nextSlide);

      dots.forEach(dot=>{
        dot.addEventListener('click', ()=>{
          const idx = parseInt(dot.dataset.index);
          goToSlide(idx);
        });
      });

      // Keyboard navigation
      document.addEventListener('keydown', function(e){
        if(e.key === 'ArrowLeft') prevSlide();
        else if(e.key === 'ArrowRight') nextSlide();
      });

      // Touch/swipe
      let touchStartX = 0;
      const hero = document.getElementById('hero');
      hero.addEventListener('touchstart', (e)=>{ touchStartX = e.touches[0].clientX; }, {passive:true});
      hero.addEventListener('touchend', (e)=>{
        const diff = touchStartX - e.changedTouches[0].clientX;
        if(Math.abs(diff) > 50){
          if(diff > 0) nextSlide();
          else prevSlide();
        }
      }, {passive:true});

      // Auto-advance
      function resetAutoAdvance(){
        clearTimeout(autoTimer);
        autoTimer = setTimeout(nextSlide, AUTO_INTERVAL);
      }

      // Pause on hover
      hero.addEventListener('mouseenter', ()=>{ clearTimeout(autoTimer); });
      hero.addEventListener('mouseleave', ()=>{ resetAutoAdvance(); });

      // Start auto-advance
      resetAutoAdvance();
    })();

    // ── Scroll & reveal ──
    (function(){
      const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const hero = document.getElementById('hero');
      // Use active slide's bg and glow for parallax
      function getActiveSlideEls(){
        const active = document.querySelector('.hero-slide.active');
        return {
          bg: active ? active.querySelector('.slide-bg') : null,
          glow: active ? active.querySelector('.slide-glow') : null,
        };
      }
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

        const els = getActiveSlideEls();
        if(els.bg) els.bg.style.transform = 'translateY('+(p * 50)+'px) scale('+(1 + p * 0.04)+')';

        const ringIntensity = Math.max(0, 1 - Math.abs(p - 0.15) * 6);
        if(els.glow){
          els.glow.style.opacity = String(Math.min(1, ringIntensity * 1.5));
          els.glow.style.transform = 'translate(-50%,-50%) scale('+(1 + ringIntensity * 0.08)+')';
        }

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
