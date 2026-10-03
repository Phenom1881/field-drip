/* Field Drip: filters, sorting, countdowns, and the school finder.
   Everything still reads fine with this file turned off; it only adds the interactive parts. */
(function () {
  'use strict';

  var DAY = 86400000;

  function each(list, fn) { Array.prototype.forEach.call(list, fn); }
  function todayStart() { var d = new Date(); d.setHours(0, 0, 0, 0); return d; }
  function parseDate(text) {
    var p = (text || '').split('-');
    return new Date(+p[0], (+p[1] || 1) - 1, +p[2] || 1);
  }
  function listHas(el, key, value) {
    if (!value) return true;
    return (el.getAttribute('data-' + key) || '').split('|').indexOf(value) !== -1;
  }

  /* Countdowns: <div data-countdown="2027-07-01"> with a [data-countdown-num] inside */
  each(document.querySelectorAll('[data-countdown]'), function (box) {
    var days = Math.max(0, Math.round((parseDate(box.getAttribute('data-countdown')) - todayStart()) / DAY));
    var num = box.querySelector('[data-countdown-num]');
    var unit = box.querySelector('[data-countdown-unit]');
    if (num) num.textContent = String(days);
    if (unit) unit.textContent = days === 1 ? 'day' : 'days';
  });

  /* Brand and conference filters, plus sorting, inside one [data-filter-scope] */
  function compareBy(key) {
    return function (a, b) {
      var schoolA = a.getAttribute('data-sort-school') || '';
      var schoolB = b.getAttribute('data-sort-school') || '';
      var rankA = +a.getAttribute('data-sort-rank') || 0;
      var rankB = +b.getAttribute('data-sort-rank') || 0;
      var startA = a.getAttribute('data-sort-start') || '';
      var startB = b.getAttribute('data-sort-start') || '';
      if (key === 'school') return schoolA.localeCompare(schoolB);
      if (key === 'status') return (rankA - rankB) || schoolA.localeCompare(schoolB);
      return startB.localeCompare(startA) || (rankA - rankB) || schoolA.localeCompare(schoolB);
    };
  }

  each(document.querySelectorAll('[data-filter-scope]'), function (scope) {
    var state = { brands: '', confs: '' };
    var chips = scope.querySelectorAll('[data-filter-key]');
    var clears = scope.querySelectorAll('[data-filter-clear]');
    var sorts = scope.querySelectorAll('[data-sort-key]');

    function apply() {
      each(chips, function (chip) {
        var on = state[chip.getAttribute('data-filter-key')] === chip.getAttribute('data-filter-value');
        chip.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      each(scope.querySelectorAll('[data-filter-list]'), function (list) {
        var items = list.querySelectorAll('[data-item]');
        var shown = 0;
        each(items, function (item) {
          var ok = listHas(item, 'brands', state.brands) && listHas(item, 'confs', state.confs);
          item.hidden = !ok;
          if (ok) shown += 1;
        });
        var count = list.querySelector('[data-count]');
        if (count) count.textContent = 'Showing ' + shown + ' of ' + items.length + ' ' + (list.getAttribute('data-noun') || 'items');
        var empty = list.querySelector('[data-empty]');
        if (empty) empty.hidden = shown > 0;
      });
      var active = !!(state.brands || state.confs);
      each(clears, function (btn) { btn.hidden = !active; });
    }

    each(chips, function (chip) {
      chip.addEventListener('click', function () {
        state[chip.getAttribute('data-filter-key')] = chip.getAttribute('data-filter-value');
        apply();
      });
    });
    each(clears, function (btn) {
      btn.addEventListener('click', function () { state.brands = ''; state.confs = ''; apply(); });
    });
    each(sorts, function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-sort-key');
        each(sorts, function (other) { other.setAttribute('aria-pressed', other === btn ? 'true' : 'false'); });
        each(scope.querySelectorAll('[data-sortable]'), function (container) {
          var items = Array.prototype.filter.call(container.children, function (el) { return el.hasAttribute('data-item'); });
          items.sort(compareBy(key));
          each(items, function (el) { container.appendChild(el); });
        });
      });
    });
    apply();
  });

  /* The tracker table: search, filters, and sortable columns */
  each(document.querySelectorAll('[data-tracker]'), function (root) {
    var tbody = root.querySelector('tbody');
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.rows);
    var search = root.querySelector('[data-tracker-search]');
    var filters = root.querySelectorAll('[data-tracker-filter]');
    var count = root.querySelector('[data-tracker-count]');
    var empty = root.querySelector('[data-tracker-empty]');
    var heads = root.querySelectorAll('th[data-col]');

    var query = new URLSearchParams(window.location.search).get('q');
    if (search && query) search.value = query;

    function apply() {
      var q = search ? search.value.trim().toLowerCase() : '';
      var shown = 0;
      rows.forEach(function (row) {
        var ok = !q || (row.getAttribute('data-search') || '').toLowerCase().indexOf(q) !== -1;
        each(filters, function (select) {
          if (ok && select.value && !listHas(row, select.getAttribute('data-tracker-filter'), select.value)) ok = false;
        });
        row.hidden = !ok;
        if (ok) shown += 1;
      });
      if (count) count.textContent = 'Showing ' + shown + ' of ' + rows.length + ' programs';
      if (empty) empty.hidden = shown > 0;
    }

    each(heads, function (th) {
      var button = th.querySelector('button');
      if (!button) return;
      button.addEventListener('click', function () {
        var col = th.getAttribute('data-col');
        var numeric = th.getAttribute('data-type') === 'number';
        var dir = th.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
        each(heads, function (h) { h.removeAttribute('aria-sort'); });
        th.setAttribute('aria-sort', dir);
        var sign = dir === 'ascending' ? 1 : -1;
        rows.sort(function (a, b) {
          var va = a.getAttribute('data-sort-' + col) || '';
          var vb = b.getAttribute('data-sort-' + col) || '';
          if (!va && vb) return 1;
          if (va && !vb) return -1;
          var diff = numeric ? (+va - +vb) : va.localeCompare(vb);
          return diff * sign || (a.getAttribute('data-sort-school') || '').localeCompare(b.getAttribute('data-sort-school') || '');
        });
        rows.forEach(function (row) { tbody.appendChild(row); });
      });
    });

    if (search) search.addEventListener('input', apply);
    each(filters, function (select) { select.addEventListener('change', apply); });
    apply();
  });

  /* School finder: pick a name to jump to its page, or search the tracker */
  each(document.querySelectorAll('[data-school-finder]'), function (input) {
    var list = document.getElementById(input.getAttribute('list'));
    function go(force) {
      var value = input.value.trim();
      if (!value) return;
      var options = list ? list.options : [];
      for (var i = 0; i < options.length; i += 1) {
        if (options[i].value.toLowerCase() === value.toLowerCase()) {
          window.location.href = options[i].getAttribute('data-url');
          return;
        }
      }
      if (force) window.location.href = input.getAttribute('data-fallback') + '?q=' + encodeURIComponent(value);
    }
    input.addEventListener('change', function () { go(false); });
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); go(true); }
    });
  });
})();
