/* Переписка по проекту: быстро настолько, чтобы туда хотелось писать.

   Это не украшение. В чат, который думает секунду на каждое сообщение,
   просто перестают писать и возвращаются в мессенджер — а вместе с ними
   уходит и вся доказательная база, ради которой он затевался.

   Три вещи, из которых складывается ощущение скорости:

   1. **Сообщение появляется сразу.** Не после ответа сервера, а в момент
      нажатия — бледным, пока не подтвердится. Ответ приходит через
      сотню миллисекунд и просто заменяет черновик настоящим.
   2. **Новые дозагружаются одним коротким запросом** «что появилось
      после N-го». Обычно он возвращает пустой список ценой в пару сотен
      байт — и это дешевле, чем заставлять человека жать «обновить».
   3. **Опрос замолкает, когда вкладку не смотрят.** Открытый в фоне
      кабинет не должен ни греть телефон, ни стучаться в сервер сутки.

   Без этого файла переписка работает обычной формой с перезагрузкой:
   писать можно, написанное сохраняется. */
(function () {
  'use strict';

  var box = document.querySelector('[data-chat]');
  if (!box) return;

  var list = box.querySelector('[data-chat-list]');
  var form = box.querySelector('[data-chat-form]');
  var field = box.querySelector('[data-chat-text]');
  var picker = box.querySelector('[data-chat-files]');
  var picked = box.querySelector('[data-chat-picked]');
  var moreBtn = box.querySelector('[data-chat-more]');

  var lastId = parseInt(box.dataset.last, 10) || 0;
  var firstId = parseInt(box.dataset.first, 10) || 0;

  /* ---------- Мелочи, без которых неудобно ---------- */

  function token() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    var input = form.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  // Поле растёт под текст. Однострочное поле для сообщения в три абзаца
  // — это набор вслепую.
  function grow() {
    field.style.height = 'auto';
    field.style.height = Math.min(field.scrollHeight, 220) + 'px';
  }

  function atBottom() {
    // Человек мог отлистать вверх и читать старое. Дёргать его вниз
    // из-за чужого сообщения нельзя — он потеряет место.
    return list.scrollHeight - list.scrollTop - list.clientHeight < 80;
  }

  function toBottom() {
    list.scrollTop = list.scrollHeight;
  }

  function dropEmpty() {
    var empty = list.querySelector('[data-chat-empty]');
    if (empty) empty.remove();
  }

  function addHtml(html, where) {
    var holder = document.createElement('div');
    holder.innerHTML = html;
    var node = holder.firstElementChild;
    if (!node) return null;
    if (where === 'top') list.prepend(node); else list.appendChild(node);
    return node;
  }

  /* ---------- Отправка ---------- */

  function draft(text, files) {
    /* Черновик — то же сообщение, только бледное и без времени.
       Рисуем его руками, потому что настоящую разметку даст сервер;
       здесь важно лишь, чтобы человек сразу увидел, что его услышали. */
    var li = document.createElement('li');
    li.className = 'msg msg--mine is-draft';

    var body = document.createElement('div');
    body.className = 'msg__body';

    if (text) {
      var p = document.createElement('p');
      p.className = 'msg__text';
      p.textContent = text;      // именно textContent: чужой разметке здесь не место
      body.appendChild(p);
    }
    if (files && files.length) {
      var note = document.createElement('p');
      note.className = 'msg__meta';
      note.textContent = files.length === 1
        ? files[0].name
        : 'файлов: ' + files.length;
      body.appendChild(note);
    }

    var meta = document.createElement('p');
    meta.className = 'msg__meta';
    meta.textContent = 'отправляю…';
    body.appendChild(meta);

    li.appendChild(body);
    dropEmpty();
    list.appendChild(li);
    toBottom();
    return li;
  }

  function send() {
    var text = field.value.trim();
    var files = picker && picker.files ? picker.files : [];
    if (!text && !files.length) return;

    var body = new FormData(form);
    var ghost = draft(text, files);

    // Поле очищаем сразу. Ждать ответа, чтобы человек мог начать писать
    // следующее, — это ровно то ощущение тормозов, от которого уходят.
    field.value = '';
    if (picker) picker.value = '';
    showPicked();
    grow();

    fetch(form.action, {
      method: 'POST',
      body: body,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': token() }
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || 'отказ');
        var real = addHtml(data.html);
        ghost.remove();
        if (real) lastId = Math.max(lastId, data.id);
        if (!firstId) firstId = data.id;
        toBottom();
      })
      .catch(function () {
        /* Врать, что отправилось, нельзя: человек закроет вкладку
           в уверенности, что написал. Черновик остаётся на экране
           и честно говорит, что не ушёл, — вместе с текстом, который
           можно скопировать. */
        ghost.classList.add('is-failed');
        var meta = ghost.querySelector('.msg__meta:last-child');
        if (meta) meta.textContent = 'не отправилось — проверьте связь';
      });
  }

  function showPicked() {
    if (!picked || !picker) return;
    var files = picker.files;
    if (!files || !files.length) { picked.textContent = ''; return; }
    picked.textContent = files.length === 1
      ? files[0].name
      : 'выбрано файлов: ' + files.length;
  }

  /* ---------- Дозагрузка новых ---------- */

  var timer = null;
  var FAST = 4000;    // пока вкладку смотрят
  var idle = 0;       // сколько пустых ответов подряд

  function poll() {
    if (document.hidden) return;

    fetch(box.dataset.since + '?after=' + lastId, {
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) { idle++; return; }
        markRead(data.read);
        if (!data.items.length) { idle++; return; }
        idle = 0;
        var stick = atBottom();
        dropEmpty();
        data.items.forEach(function (html) { addHtml(html); });
        lastId = data.last;
        if (!firstId) firstId = data.last;
        if (stick) toBottom();
      })
      .catch(function () { idle++; });
  }

  /* Отметка «прочитано» ставится на уже показанные сообщения. Она
     меняется без единого нового сообщения — собеседник просто открыл
     кабинет, — поэтому приходит отдельным списком номеров. */
  function markRead(ids) {
    if (!ids || !ids.length) return;
    ids.forEach(function (id) {
      var node = list.querySelector('[data-msg="' + id + '"]');
      if (!node || node.querySelector('.msg__read')) return;
      var meta = node.querySelector('.msg__meta');
      if (!meta) return;
      var mark = document.createElement('span');
      mark.className = 'msg__read';
      mark.textContent = 'прочитано';
      meta.appendChild(mark);
    });
  }

  function tick() {
    poll();
    // Тишина — повод спрашивать реже: в проекте, где переписываются
    // раз в день, опрос каждые четыре секунды греет телефон впустую.
    // При новом сообщении шаг возвращается к четырём секундам.
    var wait = Math.min(FAST * Math.max(1, Math.floor(idle / 5) + 1), 30000);
    timer = setTimeout(tick, wait);
  }

  /* ---------- Что было раньше ---------- */

  function older() {
    moreBtn.disabled = true;
    fetch(box.dataset.older + '?before=' + firstId, {
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        // Держим место: без этого лента прыгает, и человек теряет строку,
        // на которой остановился.
        var was = list.scrollHeight;
        data.items.slice().reverse().forEach(function (html) {
          addHtml(html, 'top');
        });
        list.scrollTop += list.scrollHeight - was;
        firstId = data.first;
        if (!data.more) moreBtn.remove(); else moreBtn.disabled = false;
      })
      .catch(function () { moreBtn.disabled = false; });
  }

  /* ---------- Пуск ---------- */

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    send();
  });

  field.addEventListener('input', grow);
  field.addEventListener('keydown', function (e) {
    // Ctrl+Enter отправляет, просто Enter переносит строку. Наоборот
    // делать нельзя: половина сообщений здесь длиннее одной строки,
    // и отправка на Enter режет их на куски.
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      send();
    }
  });

  if (picker) picker.addEventListener('change', showPicked);
  if (moreBtn) moreBtn.addEventListener('click', older);

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    // Вернулись во вкладку — спрашиваем сразу, а не через паузу:
    // первое, что делает человек, это смотрит, не написали ли ему.
    idle = 0;
    poll();
  });

  grow();
  toBottom();
  timer = setTimeout(tick, FAST);
})();
