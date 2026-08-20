/* Кабинет: переключение этапов и действия без перезагрузок.

   Ничего из этого не является условием работы. Без JavaScript кабинет
   остаётся кабинетом: этапы открыты все сразу, ссылки шкалы работают
   якорями, формы отправляются обычным POST с возвратом на тот же этап.
   Пользуются кабинетом с телефона в дороге, где связь рвётся, — и
   ломаться от этого он не должен.

   С JavaScript то же самое происходит плавно и на месте. */
(function () {
  'use strict';

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var stages = document.querySelector('[data-stages]');
  if (!stages) return;

  var rail = document.querySelector('[data-rail]');

  /* ---------- Токен формы берём из cookie, а не из разметки ----------
     Разметка стареет вместе со вкладкой. На телефоне вкладки живут
     месяцами, и токен из давно открытой страницы к моменту отправки
     уже недействителен — это выливается в «Ошибка проверки CSRF» ровно
     тогда, когда человек наконец что-то нажал. */
  function token() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    var field = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return field ? field.value : '';
  }

  /* ---------- Какой этап открыт ---------- */

  function panels() {
    return Array.prototype.slice.call(stages.querySelectorAll('[data-stage]'));
  }

  function show(id, push) {
    var found = false;
    panels().forEach(function (panel) {
      var mine = panel.dataset.stage === id;
      panel.hidden = !mine;
      if (mine) found = true;
    });
    if (!found) return false;

    if (rail) {
      rail.querySelectorAll('[data-rail-link]').forEach(function (link) {
        link.classList.toggle('is-open', link.dataset.railLink === id);
      });
    }
    if (push && window.history && window.history.replaceState) {
      // replaceState, а не push: иначе «назад» после десяти нажатий
      // по шкале уводит не с сайта, а на девять этапов назад, и человек
      // жмёт кнопку до посинения.
      window.history.replaceState(null, '', '#' + id);
    }
    return true;
  }

  function currentId() {
    var hash = (window.location.hash || '').replace('#', '');
    if (hash && document.querySelector('[data-stage="' + hash + '"]')) return hash;
    var open = stages.querySelector('.stage.is-current');
    if (open) return open.dataset.stage;
    var first = stages.querySelector('[data-stage]');
    return first ? first.dataset.stage : '';
  }

  function bindRail() {
    if (!rail) return;
    rail.querySelectorAll('[data-rail-link]').forEach(function (link) {
      link.addEventListener('click', function (e) {
        e.preventDefault();
        show(link.dataset.railLink, true);
      });
    });
  }

  /* ---------- Действия ----------
     Форма уходит запросом, сервер отвечает перерисованной карточкой
     и шкалой. Собирает разметку он же: шаблон один, и второй его копии
     здесь не появляется. */

  function busy(form, on) {
    form.classList.toggle('is-busy', on);
    form.querySelectorAll('button').forEach(function (b) { b.disabled = on; });
  }

  function swap(data) {
    if (data.rail && rail) {
      rail.outerHTML = data.rail;
      rail = document.querySelector('[data-rail]');
      bindRail();
    }
    var old = stages.querySelector('[data-stage="' + data.stage_id + '"]');
    if (old) {
      var holder = document.createElement('div');
      holder.innerHTML = data.stage;
      var fresh = holder.firstElementChild;
      old.replaceWith(fresh);
      if (!reduced) fresh.classList.add('is-fresh');
    }
    show(data.stage_id, false);
  }

  /* Какую кнопку нажали — часть отправления, а не мелочь.

     new FormData(form) собирает только поля и НЕ кладёт туда имя и
     значение нажатой кнопки. Обычная отправка формы их кладёт, поэтому
     без скрипта всё работает, а со скриптом status до сервера не доходит
     вовсе — и сервер отвечает «такого статуса нет». Ошибка ровно того
     сорта, которую не видно ни в разметке, ни в коде обработчика.

     Второй аргумент FormData делает это сам, но появился он недавно;
     на браузере постарше дописываем руками. */
  function bodyOf(form, submitter) {
    var body;
    try {
      body = new FormData(form, submitter);
      if (submitter && submitter.name && !body.has(submitter.name)) {
        body.append(submitter.name, submitter.value);
      }
    } catch (e) {
      body = new FormData(form);
      if (submitter && submitter.name) {
        body.append(submitter.name, submitter.value);
      }
    }
    return body;
  }

  function send(form, submitter) {
    var body = bodyOf(form, submitter);
    busy(form, true);

    fetch(form.action, {
      method: 'POST',
      body: body,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': token() }
    })
      .then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); })
      .then(function (pair) {
        var ok = pair[0], data = pair[1];
        if (!ok || !data.ok) {
          note(data.error || 'Не сохранилось. Попробуйте ещё раз.');
          return;
        }
        swap(data);
        if (form.hasAttribute('data-reset')) form.reset();
      })
      .catch(function () {
        // Связь оборвалась. Врать, что сохранилось, нельзя: человек
        // закроет вкладку и будет считать задачу закрытой.
        note('Связь пропала. Отметка не сохранилась.');
      })
      .then(function () { busy(form, false); });
  }

  var noteBox = null;
  function note(text) {
    if (!noteBox) {
      noteBox = document.createElement('p');
      noteBox.className = 'cab-note';
      noteBox.setAttribute('role', 'status');
      document.body.appendChild(noteBox);
    }
    noteBox.textContent = text;
    noteBox.classList.add('is-on');
    clearTimeout(noteBox._timer);
    noteBox._timer = setTimeout(function () {
      noteBox.classList.remove('is-on');
    }, 4000);
  }

  // Слушаем на всём кабинете, а не на каждой форме: карточка этапа
  // заменяется целиком, и обработчики, навешенные на её формы, уехали
  // бы вместе с ней.
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.matches || !form.matches('[data-act]')) return;
    e.preventDefault();
    send(form, e.submitter);
  });

  /* ---------- Копирование готового сообщения ---------- */
  document.addEventListener('click', function (e) {
    var button = e.target.closest ? e.target.closest('[data-copy]') : null;
    if (!button) return;
    var source = document.querySelector(button.dataset.copy);
    if (!source) return;

    var done = function () {
      var was = button.textContent;
      button.textContent = 'Скопировано';
      setTimeout(function () { button.textContent = was; }, 1800);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(source.value).then(done, function () {
        source.select();
      });
    } else {
      // Старый способ. Он же — запасной: буфер обмена недоступен
      // на страницах без HTTPS, а такое бывает при отладке.
      source.select();
      try { document.execCommand('copy'); done(); } catch (err) { /* ну и ладно */ }
    }
  });

  /* ---------- Пуск ---------- */
  bindRail();
  show(currentId(), false);
  window.addEventListener('hashchange', function () { show(currentId(), false); });
})();
