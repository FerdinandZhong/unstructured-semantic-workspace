// Fixed UI text in English or Simplified Chinese (plan 2026-11-23-v2-zh-amp, step Z2). Loaded first; the dictionary
// files in i18n/ register after it, before any page script runs. Convention and rules: i18n/README.md.
//   t('Confirm write')                       English source text is the key; missing entry -> the English text.
//   t('{count} rows in {table}', {count, table})   {name} placeholders are filled from vars (values used as given).
//   V2I18n.server(text)                     text the server sent (a code, a label, a sentence) -> Chinese or as sent.
//   V2I18n.number(n, options)               Intl.NumberFormat for the current language.
//   V2I18n.setLang('zh-CN' | 'en')          stores the choice in localStorage['v2.lang'] and reloads the page.
const V2I18n = (() => {
  const LANGS = {en: 'English', 'zh-CN': '中文'};
  let lang = 'en';
  try { lang = LANGS[localStorage.getItem('v2.lang')] ? localStorage.getItem('v2.lang') : 'en'; } catch { /* storage blocked */ }
  document.documentElement.lang = lang;
  const dicts = {};
  let patterns = null;              // [regex, names, template] for keys with {name}, built on first use

  const fill = (text, vars) => vars ? text.replace(/\{(\w+)\}/g, (all, name) => name in vars ? String(vars[name]) : all) : text;
  const entry = key => lang === 'en' ? undefined : dicts[lang]?.[key];

  function t(key, vars) {
    return fill(entry(key) || key, vars);
  }

  function server(text) {
    if (typeof text !== 'string' || lang === 'en') return text;
    const exact = entry(text);
    if (exact) return exact;
    if (!patterns) {
      // A key is a pattern only when it starts with literal text and holds at least 8 literal characters: a short key
      // such as "{count} rows" or "{label} by {axis}" would otherwise rewrite any sentence that ends the same way.
      const pattern = key => /\{\w+\}/.test(key) && !key.startsWith('{') && key.replace(/\{\w+\}/g, '').trim().length >= 8;
      patterns = Object.entries(dicts[lang] || {}).filter(([key]) => pattern(key)).map(([key, value]) => {
        const names = [];
        const source = key.split(/(\{\w+\})/).map(part => /^\{\w+\}$/.test(part)
          ? (names.push(part.slice(1, -1)), '(.+?)') : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('');
        return [new RegExp('^' + source + '$', 's'), names, value];
      });
    }
    for (const [regex, names, value] of patterns) {
      const match = regex.exec(text);
      if (match) return fill(value, Object.fromEntries(names.map((name, i) => [name, match[i + 1]])));
    }
    return text;
  }

  return {
    get lang() { return lang; },
    languages: LANGS,
    t, server,
    register(language, entries) { dicts[language] = Object.assign(dicts[language] || {}, entries); patterns = null; },
    number: (n, options) => new Intl.NumberFormat(lang === 'en' ? 'en-GB' : lang, options).format(n),
    // fetch options with the X-V2-Lang header (plan D3, D4). English: the options as given, so no request changes.
    withLang: options => lang === 'en' ? options : {...options, headers: {...options?.headers, 'X-V2-Lang': lang}},
    setLang(next) {
      if (!LANGS[next] || next === lang) return;
      try { localStorage.setItem('v2.lang', next); } catch { /* storage blocked: the choice lasts this page only */ }
      lang = next;
      location.reload();
    }
  };
})();
window.V2I18n = V2I18n;
window.t = V2I18n.t;
