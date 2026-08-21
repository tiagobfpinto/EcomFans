/* EcomFans landing — sticky nav, mobile drawer, one restrained scroll reveal. */
(function () {
  'use strict';

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── Nav: hairline + blur once the page has moved ───────────── */
  var nav = document.getElementById('nav');
  if (nav) {
    var onScroll = function () {
      nav.classList.toggle('is-stuck', window.scrollY > 8);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  /* ── Mobile drawer ──────────────────────────────────────────── */
  var burger = document.getElementById('navBurger');
  var drawer = document.getElementById('navDrawer');
  if (burger && drawer) {
    var setDrawer = function (open) {
      drawer.classList.toggle('is-open', open);
      burger.setAttribute('aria-expanded', String(open));
      burger.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    };
    burger.addEventListener('click', function () {
      setDrawer(!drawer.classList.contains('is-open'));
    });
    drawer.addEventListener('click', function (e) {
      if (e.target.closest('a')) setDrawer(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && drawer.classList.contains('is-open')) {
        setDrawer(false);
        burger.focus();
      }
    });
  }

  /* ── Reveal on scroll ───────────────────────────────────────── */
  var revealed = document.querySelectorAll('.rise, .close');

  if (reduced || !('IntersectionObserver' in window)) {
    revealed.forEach(function (el) { el.classList.add('is-seen'); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-seen');
        io.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -12% 0px', threshold: 0.12 });

    revealed.forEach(function (el) { io.observe(el); });
  }

  /* ── Mark the section you're reading ────────────────────────── */
  var links = Array.prototype.slice.call(document.querySelectorAll('.nav__links a'));
  var sections = links
    .map(function (link) { return document.querySelector(link.getAttribute('href')); })
    .filter(Boolean);

  if (sections.length) {
    var highlight = function () {
      var line = window.scrollY + window.innerHeight * 0.3;
      var current = -1;
      sections.forEach(function (section, i) {
        if (section.offsetTop <= line) current = i;
      });
      links.forEach(function (link, i) {
        link.classList.toggle('is-active', i === current);
      });
    };
    highlight();
    window.addEventListener('scroll', highlight, { passive: true });
  }
})();
