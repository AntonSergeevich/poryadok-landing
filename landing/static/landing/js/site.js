/* Системы Порядок — минимальный клиентский слой.
   Всё содержимое доступно и без JS: скрипт только помогает. */
(function () {
  'use strict';

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- Меню ---------- */
  var head = document.getElementById('head');
  var burger = document.getElementById('burger');
  if (head && burger) {
    var close = function () {
      head.classList.remove('is-open');
      burger.setAttribute('aria-expanded', 'false');
    };
    burger.addEventListener('click', function () {
      var open = head.classList.toggle('is-open');
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    head.querySelectorAll('.nav a, .head .btn--sm').forEach(function (a) {
      a.addEventListener('click', close);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && head.classList.contains('is-open')) {
        close();
        burger.focus();
      }
    });
  }

  /* ---------- Маска и проверка телефона ---------- */
  function mask(digits) {
    var d = digits.replace(/\D/g, '');
    if (d[0] === '8' || d[0] === '7') d = d.slice(1);
    d = d.slice(0, 10);
    if (!d) return '';
    var out = '+7 (' + d.slice(0, 3);
    if (d.length >= 4) out += ') ' + d.slice(3, 6);
    if (d.length >= 7) out += '-' + d.slice(6, 8);
    if (d.length >= 9) out += '-' + d.slice(8, 10);
    return out;
  }

  document.querySelectorAll('[data-validate="phone"] input').forEach(function (input) {
    var field = input.closest('.field');
    input.addEventListener('input', function () {
      input.value = mask(input.value);
      field.classList.remove('is-bad');
    });
    input.addEventListener('focus', function () {
      if (!input.value) input.value = '+7 (';
    });
    input.addEventListener('blur', function () {
      if (input.value === '+7 (') input.value = '';
    });

    var form = input.form;
    if (!form) return;
    form.addEventListener('submit', function (e) {
      if (input.value.length !== 18) {
        e.preventDefault();
        field.classList.add('is-bad');
        input.focus();
      }
    });
  });

  /* ---------- Загрузка файла ---------- */
  var drop = document.getElementById('drop');
  var file = document.getElementById('sales-file');
  if (drop && file) {
    var idle = document.getElementById('drop-idle');
    var filled = document.getElementById('drop-filled');
    var nameEl = document.getElementById('drop-name');

    var show = function (name) {
      idle.hidden = true;
      filled.hidden = false;
      nameEl.textContent = name;
    };

    drop.addEventListener('click', function () { file.click(); });
    drop.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.click(); }
    });
    file.addEventListener('change', function () {
      if (file.files.length) show(file.files[0].name);
    });

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); e.stopPropagation(); });
    });
    ['dragenter', 'dragover'].forEach(function (ev) {
      drop.addEventListener(ev, function () { drop.classList.add('is-over'); });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      drop.addEventListener(ev, function () { drop.classList.remove('is-over'); });
    });
    drop.addEventListener('drop', function (e) {
      if (e.dataTransfer.files.length) {
        file.files = e.dataTransfer.files;
        show(e.dataTransfer.files[0].name);
      }
    });
  }

  /* ---------- Портрет: аккуратная заглушка, если файла ещё нет ---------- */
  var portraitImg = document.getElementById('portrait-img');
  if (portraitImg) {
    var markEmpty = function () { document.getElementById('portrait').classList.add('is-empty'); };
    portraitImg.addEventListener('error', markEmpty);
    if (portraitImg.complete && portraitImg.naturalWidth === 0) markEmpty();
  }

  /* ---------- Появление блоков ---------- */
  var root = document.documentElement;
  if (reduced || !('IntersectionObserver' in window)) {
    root.className = '';          // показываем содержимое как есть
    root.dataset.ready = '1';
    return;
  }
  root.dataset.ready = '1';       // отменяем страховочный таймер из <head>

  var flow = document.getElementById('flow');
  if (flow) requestAnimationFrame(function () { flow.classList.add('is-set'); });

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-in');
        io.unobserve(entry.target);
      }
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });

  document.querySelectorAll('.rv').forEach(function (el) { io.observe(el); });
})();
