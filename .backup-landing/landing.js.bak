/* ============================================================
   EcomFans — Landing Page JS
   ============================================================ */
(function () {
  'use strict';

  /* ── Nav scroll effect ────────────────────────────────────── */
  const nav = document.getElementById('lpNav');
  if (nav) {
    const onScroll = () => {
      nav.classList.toggle('scrolled', window.scrollY > 20);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  /* ── Mobile nav burger ────────────────────────────────────── */
  const burger = document.getElementById('navBurger');
  const mobileMenu = document.getElementById('navMobile');
  if (burger && mobileMenu) {
    burger.addEventListener('click', () => {
      const open = burger.classList.toggle('open');
      mobileMenu.classList.toggle('open', open);
      burger.setAttribute('aria-expanded', open);
    });
    // Close on link click
    mobileMenu.querySelectorAll('a').forEach(link => {
      link.addEventListener('click', () => {
        burger.classList.remove('open');
        mobileMenu.classList.remove('open');
      });
    });
  }

  /* ── Smooth scroll for anchor links ──────────────────────── */
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      const target = document.querySelector(this.getAttribute('href'));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });

  /* ── Scroll reveal ────────────────────────────────────────── */
  const revealEls = () => {
    document.querySelectorAll('.workflow-step, .feature-card, .testi-card, .price-card, .wf-node, .variant-card, .demo-variant-card').forEach(el => {
      el.classList.add('reveal');
    });
  };

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry, i) => {
        if (entry.isIntersecting) {
          setTimeout(() => {
            entry.target.classList.add('visible');
          }, (entry.target.dataset.revealDelay || 0) * 1000);
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.1, rootMargin: '0px 0px -40px 0px' }
  );

  revealEls();
  document.querySelectorAll('.reveal').forEach((el, i) => {
    // stagger siblings
    const parent = el.parentElement;
    const siblings = Array.from(parent.querySelectorAll('.reveal'));
    const idx = siblings.indexOf(el);
    el.style.transitionDelay = `${idx * 0.08}s`;
    observer.observe(el);
  });

  /* ── Pricing toggle ───────────────────────────────────────── */
  const ptSwitch = document.getElementById('ptSwitch');
  const ptMonthly = document.getElementById('ptMonthly');
  const ptYearly = document.getElementById('ptYearly');
  const priceValues = document.querySelectorAll('.price-value');

  let isYearly = false;

  const updatePrices = () => {
    priceValues.forEach(el => {
      const val = isYearly ? el.dataset.yearly : el.dataset.monthly;
      if (val) {
        // Animate number change
        animateNumber(el, parseInt(el.textContent, 10), parseInt(val, 10));
      }
    });
    ptSwitch.classList.toggle('on', isYearly);
    ptMonthly.classList.toggle('ptoggle-label--active', !isYearly);
    ptYearly.classList.toggle('ptoggle-label--active', isYearly);
  };

  const animateNumber = (el, from, to) => {
    const duration = 300;
    const start = performance.now();
    const step = (now) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = Math.round(from + (to - from) * eased);
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  if (ptSwitch) {
    ptSwitch.addEventListener('click', () => {
      isYearly = !isYearly;
      updatePrices();
    });
  }
  if (ptMonthly) {
    ptMonthly.addEventListener('click', () => {
      if (isYearly) { isYearly = false; updatePrices(); }
    });
  }
  if (ptYearly) {
    ptYearly.addEventListener('click', () => {
      if (!isYearly) { isYearly = true; updatePrices(); }
    });
  }

  /* ── Hero flow animation: stagger variant cards ───────────── */
  document.querySelectorAll('.variant-card').forEach((card, i) => {
    card.style.setProperty('--delay', `${i * 0.1}s`);
  });

  /* ── Active nav link highlight on scroll ──────────────────── */
  const sections = ['hero', 'workflow', 'features', 'demo', 'pricing'].map(id => document.getElementById(id)).filter(Boolean);
  const navLinks = document.querySelectorAll('.lp-nav__links a');

  const highlightNav = () => {
    let current = '';
    sections.forEach(section => {
      const offset = section.getBoundingClientRect().top;
      if (offset <= 120) current = section.id;
    });
    navLinks.forEach(link => {
      const href = link.getAttribute('href');
      link.style.color = (href === `#${current}`) ? 'var(--text-0)' : '';
      link.style.background = (href === `#${current}`) ? 'var(--bg-3)' : '';
    });
  };

  window.addEventListener('scroll', highlightNav, { passive: true });

})();
