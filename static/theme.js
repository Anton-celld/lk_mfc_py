/* Тема и режим встраивания. Подключается в <head> до отрисовки, чтобы страница не мигала.
   В окне личного кабинета платформы (iframe на том же адресе) тема берётся у кабинета: из его настройки portal.theme при загрузке
   и из сообщений { type: 'portal:prefs', theme: 'light' | 'dark' | 'system', lang } при каждом изменении.
   Вне кабинета страница всегда светлая (фирменный стиль). Язык сервис не переключает: интерфейс только на русском. */
(function () {
  var root = document.documentElement;
  var embedded = false;
  try { embedded = window.parent !== window; } catch (e) { /* чужой родитель */ }
  if (embedded) root.classList.add('embed');

  var system = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  var current = 'light';
  function apply(theme) {
    current = theme === 'dark' || theme === 'system' ? theme : 'light';
    var dark = current === 'dark' || (current === 'system' && !!system && system.matches);
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', dark ? '#111820' : '#ffffff');
  }

  var saved = null;
  if (embedded) { try { saved = localStorage.getItem('portal.theme'); } catch (e) { /* хранилище недоступно */ } }
  apply(embedded ? (saved === 'light' || saved === 'dark' ? saved : 'system') : 'light');

  if (embedded) {
    window.addEventListener('message', function (e) {
      // только от окна кабинета на том же адресе
      if (e.origin !== window.location.origin || e.source !== window.parent) return;
      var d = e.data;
      if (d && d.type === 'portal:prefs') apply(d.theme);
    });
    if (system && system.addEventListener) system.addEventListener('change', function () { if (current === 'system') apply('system'); });
  }
})();
