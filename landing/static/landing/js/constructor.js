/* Конструктор: схема и живой пересчёт.

   Без скрипта страница работает целиком: флажки обычные, кнопка
   «Пересчитать» настоящая, состав уезжает вместе с заявкой. Скрипт
   убирает перезагрузку и раскладывает блоки по кругу — и только.

   Считает по-прежнему сервер. Копия формулы здесь однажды разошлась бы
   с настоящей, и человек увидел бы на экране одно число, а в заявке
   приехало бы другое. Такое расхождение стоит доверия целиком. */
(function () {
  'use strict';

  var form = document.querySelector('[data-build]');
  if (!form) return;

  var scheme = form.querySelector('[data-scheme]');
  var totalBox = form.querySelector('[data-total]');
  var priceUrl = form.dataset.priceUrl;

  /* ---------- Раскладка блоков по эллипсу ----------
     Углы и координаты считаются здесь, а не прописаны в разметке:
     блоков может стать больше, и переписывать двенадцать пар значений
     руками — гарантия, что тринадцатый однажды ляжет поверх первого.

     Эллипс, а не круг: лист шире, чем высок, и на круге блоки жались бы
     по бокам, оставляя пустыми верх и низ. */
  var wires = form.querySelector('[data-wires]');

  // Ниже этого порога подписи по эллипсу налезают друг на друга и на
  // само ядро. Схема, в которой не разобрать слов, хуже честного списка.
  var FLAT_BELOW = 720;

  function layout() {
    if (!scheme) return;
    var nodes = Array.prototype.slice.call(scheme.querySelectorAll('[data-node]'));
    var box = scheme.getBoundingClientRect();
    if (!nodes.length || !box.width) return;

    if (box.width < FLAT_BELOW) {
      scheme.classList.add('is-flat');
      // Снимаем координаты: иначе после поворота телефона обратно
      // в широкий вид блоки останутся стоять по старым местам.
      nodes.forEach(function (node) { node.style.left = node.style.top = ''; });
      if (wires) { while (wires.firstChild) wires.removeChild(wires.firstChild); }
      return;
    }
    scheme.classList.remove('is-flat');

    var cx = box.width / 2;
    var cy = box.height / 2;
    // Запас по краям — половина самого широкого блока плюс поля, иначе
    // крайние уезжают за рамку листа.
    var rx = Math.max(cx - 78, 60);
    var ry = Math.max(cy - 34, 48);

    if (wires) {
      wires.setAttribute('viewBox', '0 0 ' + box.width + ' ' + box.height);
      while (wires.firstChild) wires.removeChild(wires.firstChild);
    }

    nodes.forEach(function (node, i) {
      // Начинаем сверху и идём по кругу равными долями.
      var angle = (Math.PI * 2 / nodes.length) * i - Math.PI / 2;
      var x = cx + Math.cos(angle) * rx;
      var y = cy + Math.sin(angle) * ry;

      node.style.left = x + 'px';
      node.style.top = y + 'px';

      if (!wires) return;
      // Линия идёт не в самый центр, а до края ядра: упереться
      // в середину прямоугольника она не должна.
      var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', x);
      line.setAttribute('y1', y);
      line.setAttribute('x2', cx + Math.cos(angle) * 82);
      line.setAttribute('y2', cy + Math.sin(angle) * 54);
      line.dataset.wire = node.dataset.node;
      wires.appendChild(line);
    });
  }

  /* ---------- Схема следует за флажками ---------- */
  function paint(ids) {
    if (!scheme) return;
    var on = {};
    (ids || []).forEach(function (id) { on[id] = true; });
    scheme.querySelectorAll('[data-node]').forEach(function (node) {
      node.classList.toggle('is-on', !!on[node.dataset.node]);
    });
    if (wires) {
      wires.querySelectorAll('[data-wire]').forEach(function (line) {
        line.classList.toggle('is-on', !!on[line.dataset.wire]);
      });
    }
  }

  function chosen() {
    return Array.prototype.slice
      .call(form.querySelectorAll('[data-block]'))
      .filter(function (input) { return input.checked || input.disabled; })
      .map(function (input) { return input.value; });
  }

  /* ---------- Пересчёт ---------- */

  function token() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    var field = form.querySelector('input[name="csrfmiddlewaretoken"]');
    return field ? field.value : '';
  }

  var pending = null;

  function recount() {
    var body = new URLSearchParams();
    chosen().forEach(function (id) { body.append('blocks', id); });
    var scale = form.querySelector('[data-scale]:checked');
    body.append('scale', scale ? scale.value : 'solo');

    // Отменяем предыдущий запрос: человек щёлкает подряд, и ответы
    // могут вернуться не в том порядке, в каком уходили. Тогда на экране
    // окажется цена от позапрошлого набора.
    if (pending) pending.abort();
    var stop = new AbortController();
    pending = stop;

    fetch(priceUrl, {
      method: 'POST',
      body: body,
      credentials: 'same-origin',
      signal: stop.signal,
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': token(),
        'Content-Type': 'application/x-www-form-urlencoded'
      }
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        paint(data.ids);
        render(data);
      })
      .catch(function () { /* отменили или связь пропала — цифра остаётся прежней */ });
  }

  /* Итог приходит готовой разметкой и подставляется целиком.

     Собирать его здесь по цифрам уже пробовали: строка про скидку
     не появлялась вовсе — её не было в исходной разметке, а дорисовывать
     её скрипт не умел. Пока итог собирается в двух местах, такие
     расхождения будут возвращаться. Шаблон должен быть один. */
  function render(data) {
    totalBox.innerHTML = data.html;
    totalBox.classList.add('is-new');
    clearTimeout(totalBox._timer);
    totalBox._timer = setTimeout(function () {
      totalBox.classList.remove('is-new');
    }, 500);
  }

  /* ---------- Пуск ---------- */

  form.addEventListener('change', function (e) {
    if (!e.target.matches('[data-block], [data-scale]')) return;
    paint(chosen());   // схема отвечает сразу, не дожидаясь сервера
    recount();
  });

  layout();
  paint(chosen());

  // Радиус зависит от ширины листа, а лист резиновый.
  var resizeTimer = null;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(layout, 150);
  }, { passive: true });
})();
