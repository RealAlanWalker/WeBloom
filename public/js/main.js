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

  function scrollToId(id){ document.getElementById(id).scrollIntoView({behavior:'smooth'}); }

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
