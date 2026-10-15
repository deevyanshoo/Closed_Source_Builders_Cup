/* Runs before the application bundle so the selected palette is ready for first paint. */
(function () {
  var key = 'zoorq-theme';
  var root = document.documentElement;
  var media;
  try { media = window.matchMedia('(prefers-color-scheme: dark)'); } catch (_) { media = null; }
  function normalize(value) { return value === 'dark' || value === 'system' ? value : 'light'; }
  function saved() {
    try { return normalize(window.localStorage.getItem(key)); } catch (_) { return 'light'; }
  }
  function apply(preference) {
    var choice = normalize(preference);
    var resolved = choice === 'system' ? media && media.matches ? 'dark' : 'light' : choice;
    root.setAttribute('data-zoorq-preference', choice);
    root.setAttribute('data-zoorq-theme', resolved);
    if (window.location.pathname === '/demo') {
      root.style.colorScheme = resolved;
      root.style.backgroundColor = resolved === 'dark' ? '#0c1220' : '#f7f9fc';
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.content = resolved === 'dark' ? '#0c1220' : '#FFFFFF';
    }
    try { window.dispatchEvent(new CustomEvent('zoorq-theme-change', { detail: { preference: choice, resolvedTheme: resolved } })); } catch (_) { /* Older browsers can still use the applied attributes. */ }
  }
  apply(saved());
  if (media && media.addEventListener) media.addEventListener('change', function () {
    if (root.getAttribute('data-zoorq-preference') === 'system') apply('system');
  });
  window.addEventListener('storage', function (event) {
    if (event.key === key || event.key === null) apply(event.key === key ? event.newValue : saved());
  });
})();
