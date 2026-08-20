/* Системы Порядок — минимальный клиентский слой.
   Всё содержимое доступно и без JS: скрипт только помогает. */
(function () {
  'use strict';

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- Хранилище: в приватном режиме оно может быть недоступно ---------- */
  function remember(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* переживём */ }
  }
  function recall(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }

  /* ---------- Предупреждение о cookie ---------- */
  var COOKIE_KEY = 'poryadok:cookie-ok';

  function startMetrika() {
    var id = window.__metrikaId;
    if (!id || window.__metrikaStarted) return;
    window.__metrikaStarted = true;
    // Счётчик подключается только после согласия — до него запросов нет.
    (function (m, e, t, r, i, k, a) {
      m[i] = m[i] || function () { (m[i].a = m[i].a || []).push(arguments); };
      m[i].l = 1 * new Date();
      k = e.createElement(t); a = e.getElementsByTagName(t)[0];
      k.async = 1; k.src = r; a.parentNode.insertBefore(k, a);
    })(window, document, 'script', 'https://mc.yandex.ru/metrika/tag.js', 'ym');
    window.ym(id, 'init', {
      clickmap: true, trackLinks: true, accurateTrackBounce: true, webvisor: false
    });
  }

  var cookieBox = document.getElementById('cookie');
  if (cookieBox) {
    if (recall(COOKIE_KEY) === '1') {
      startMetrika();
    } else {
      cookieBox.hidden = false;
      var okBtn = document.getElementById('cookie-ok');
      if (okBtn) {
        okBtn.addEventListener('click', function () {
          remember(COOKIE_KEY, '1');
          cookieBox.hidden = true;
          startMetrika();
        });
      }
    }
  }

  /* ---------- Липкая кнопка: показываем, когда первый экран уехал ---------- */
  var dock = document.getElementById('dock');
  if (dock) {
    var hero = document.querySelector('.hero');
    var toggleDock = function () {
      var passed = hero ? (hero.getBoundingClientRect().bottom < 0) : (window.scrollY > 400);
      dock.hidden = !passed;
    };
    toggleDock();
    window.addEventListener('scroll', toggleDock, { passive: true });
  }

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
    head.querySelectorAll('.head__menu a').forEach(function (a) {
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

  document.querySelectorAll('[data-validate="phone-soft"] input').forEach(function (input) {
    input.addEventListener('input', function () {
      input.value = mask(input.value);
      input.closest('.field').classList.remove('is-bad');
    });
  });

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

  /* ---------- Разбор процессов: один вопрос за раз ---------- */
  /* Без скрипта форма работает как есть — все вопросы подряд и одна кнопка.
     Скрипт лишь показывает их по одному: так короче и не пугает длиной. */
  var quiz = document.getElementById('q-steps');
  if (quiz) {
    var steps = Array.prototype.slice.call(quiz.querySelectorAll('.q-step'));
    var bar = document.getElementById('q-bar');
    var fill = document.getElementById('q-fill');
    var now = document.getElementById('q-now');
    var total = steps.length;
    var at = 0;

    // Если сервер вернул форму с ошибками, открываем первый спорный вопрос.
    var bad = quiz.querySelector('.err--on');
    if (bad) {
      var owner = bad.closest('.q-step');
      if (owner) at = steps.indexOf(owner);
    }

    var show = function (i, focus) {
      at = Math.max(0, Math.min(i, total - 1));
      steps.forEach(function (el, n) { el.classList.toggle('is-now', n === at); });
      if (fill) fill.style.width = ((at + 1) / total * 100) + '%';
      // Последний лист — не вопрос, а контакты: считать его шестнадцатым
      // из пятнадцати было бы враньём.
      if (now) {
        var asked = bar ? Number(bar.dataset.questions) : total;
        now.textContent = (at + 1 > asked)
          ? 'Последний шаг'
          : 'Вопрос ' + (at + 1) + ' из ' + asked;
      }
      if (focus) {
        var first = steps[at].querySelector('input, textarea');
        if (first && first.type !== 'checkbox' && first.type !== 'radio') first.focus();
      }
      var head = document.getElementById('head');
      var top = quiz.getBoundingClientRect().top + window.scrollY
              - (head ? head.offsetHeight + 56 : 80);
      if (window.scrollY > top) window.scrollTo({ top: top, behavior: reduced ? 'auto' : 'smooth' });
    };

    document.documentElement.classList.add('q-js');
    if (bar) bar.hidden = false;

    steps.forEach(function (step, n) {
      var next = step.querySelector('.q-next');
      var back = step.querySelector('.q-back');
      if (next) { next.hidden = false; next.addEventListener('click', function () { show(n + 1, true); }); }
      if (back && n > 0) { back.hidden = false; back.addEventListener('click', function () { show(n - 1, false); }); }

      // Один вариант из списка — ответ дан, идём дальше сами.
      // Для нескольких вариантов и для «Другое» так делать нельзя:
      // человек ещё не закончил отвечать.
      step.querySelectorAll('input[type=radio]').forEach(function (radio) {
        radio.addEventListener('change', function () {
          if (radio.value === 'other') {
            var own = step.querySelector('.q-other');
            if (own) own.focus();
            return;
          }
          setTimeout(function () { show(n + 1, false); }, 180);
        });
      });
    });

    // Клавиатура: цифра выбирает вариант, Enter листает дальше.
    // Мышкой всё то же самое, просто быстрее теми, кто привык печатать.
    document.addEventListener('keydown', function (e) {
      if (!quiz.offsetParent) return;
      var tag = (e.target.tagName || '').toLowerCase();
      var typing = tag === 'input' && e.target.type === 'text'
                || tag === 'input' && e.target.type === 'tel'
                || tag === 'textarea';

      if (e.key === 'Enter' && !e.shiftKey) {
        if (tag === 'textarea') return;
        if (at < total - 1) { e.preventDefault(); show(at + 1, true); }
        return;
      }
      if (typing) return;

      if (e.key >= '1' && e.key <= '9') {
        var opts = steps[at].querySelectorAll('.q-opt input');
        var pick = opts[Number(e.key) - 1];
        if (pick) {
          e.preventDefault();
          pick.checked = pick.type === 'checkbox' ? !pick.checked : true;
          pick.dispatchEvent(new Event('change', { bubbles: true }));
        }
      }
    });

    steps.forEach(function (step) {
      var keys = step.querySelector('.q-keys');
      if (keys) keys.hidden = false;
    });

    show(at, false);
  }

  /* ---------- Портрет: аккуратная заглушка, если файла ещё нет ---------- */
  var portraitImg = document.getElementById('portrait-img');
  if (portraitImg) {
    var markEmpty = function () { document.getElementById('portrait').classList.add('is-empty'); };
    portraitImg.addEventListener('error', markEmpty);
    if (portraitImg.complete && portraitImg.naturalWidth === 0) markEmpty();
  }

  /* ---------- Снимок экрана во весь экран ----------
     Родное окно браузера <dialog>: Esc, возврат фокуса на ту же плитку
     и закрытие остального от экранного диктора достаются бесплатно.

     Если <dialog> не поддерживается (старая мобильная прошивка), не делаем
     ничего: ссылка остаётся ссылкой и снимок откроется отдельной вкладкой.
     Это хуже, но работает — а самодельное окно на таком браузере обычно
     ломается совсем. */
  var lightbox = document.getElementById('lightbox');
  if (lightbox && typeof lightbox.showModal === 'function') {
    var boxImg = document.getElementById('lightbox-img');
    var boxCap = document.getElementById('lightbox-cap');

    document.querySelectorAll('[data-shot]').forEach(function (link) {
      link.addEventListener('click', function (e) {
        e.preventDefault();
        boxImg.src = link.getAttribute('href');
        boxImg.alt = link.dataset.cap || '';
        boxCap.textContent = link.dataset.cap || '';
        lightbox.showModal();
      });
    });

    var closeBox = function () { lightbox.close(); };
    var closeBtn = lightbox.querySelector('[data-shot-close]');
    if (closeBtn) closeBtn.addEventListener('click', closeBox);

    // Нажатие мимо снимка тоже закрывает: окно занимает весь экран,
    // и цель события — само окно, только когда попали в подложку.
    lightbox.addEventListener('click', function (e) {
      if (e.target === lightbox) closeBox();
    });

    // Снимок весит под сотню килобайт. Держать его в памяти после закрытия
    // незачем, а на слабом телефоне десяток открытых подряд заметен.
    lightbox.addEventListener('close', function () {
      boxImg.removeAttribute('src');
      boxCap.textContent = '';
    });
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
