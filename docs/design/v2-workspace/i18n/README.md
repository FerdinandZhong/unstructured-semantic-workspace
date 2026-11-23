# UI languages: English and Simplified Chinese

English is the default. `i18n.js` loads first and keeps the choice in `localStorage['v2.lang']` (`en` or `zh-CN`).
The switch in the top bar stores the choice and reloads the page. The server keeps its English text (plan D2).

## API

- `t(key, vars)`: the fixed text in the current language. A missing key or entry returns the English key.
- `{name}` placeholders take their values from `vars`, inserted as given (the caller escapes them for HTML).
- `V2I18n.server(text)`: text the server sent (a code, a label or a sentence). The result is Chinese or the text as sent.
- `V2I18n.number(n, options)`: `Intl.NumberFormat` for the current language (`en-GB` for English).
- `V2I18n.lang`, `V2I18n.setLang(code)` and `V2I18n.register(lang, entries)`.
- React pages import `t` and `serverText` from `app/src/lib.js`. Call them at render time, not at module load.
- `AskProgress.jsx` is the worked example.

## Key convention

- The English source text is the key: `t('Confirm write')`. Call sites stay readable, and English needs no dictionary.
- A sentence with values is one key with named placeholders: `t('{count} rows in {table}', {count, table})`.
  Never build a sentence from translated fragments. Word order differs in Chinese.
- Long sentences are keys too. Use the whole sentence, word for word, as English shows it today.
- Avoid plurals. When English needs them, choose the key by count: `n === 1 ? t('{n} row', {n}) : t('{n} rows', {n})`.
  Prefer plural-free wording for new text, for example `Rows: {n}`.
- Server text is keyed by the code or sentence the server sends. Exact keys match first. A key with `{name}` is
  also a pattern when it starts with literal text and holds at least 8 literal characters; it matches a sentence with
  any text in each placeholder. A sentence that starts with a value (`{n} rows failed ...`) stays English.
- Model-written text and questions: every `callLocal` request (and the React `/api/ask` and write preview calls) carries
  `X-V2-Lang: zh-CN` in Chinese (`V2I18n.withLang(options)`); English sends no header. The server then asks for the
  answer summary and the grouping hint in Chinese and rewrites a Chinese question to English (`semantic_pipeline_v2/ui_lang.py`).

## Adding a dictionary file

1. Create `i18n/zh-CN.<area>.js` holding one call: `window.V2I18n?.register('zh-CN', { ... });`.
   The object is strict JSON: double quotes, no comments inside, no trailing comma. Comments go above the call.
2. Add the file to `index.html` after `i18n.js` (dictionaries in name order, before `helios-canvas.js`).
   Also add it to `ASSETS` in `semantic_pipeline_v2/workspace_server.py`.
3. One area per file, so parallel work never edits one shared file. Existing files:
   `zh-CN.server.js` (server codes and sentences), `zh-CN.shell.js` (workspace.js), `zh-CN.pipeline.js` (React pages).
4. A server started before a file joined `ASSETS` returns 404 for it. That page keeps English:
   `workspace.js` defines an English `V2I18n` and `t` when `i18n.js` is missing.

## Never translate

- Identifiers, ids, file names, table and column names, SQL, numbers, dates, units and masked values.
- Product names: Cloudera, Cloudera AI, Iceberg, Trino, Spark, PostgreSQL, Google AlloyDB, Semantic Hub, Helios.
- `data-*` attribute values, CSS class names, `aria-*` ids, route hashes, `value=` of options and buttons.
- Anything code compares: a reason code (`answer.refusalReason !== 'no supported answer'`), a status, a step key.
  Translate only where the text is shown, and keep the comparison on the English value.
- Text a test asserts in English stays the English key; the English UI must not change.

## Template-literal HTML

Translate the text nodes only. Never pass markup through a dictionary entry.
For inline markup inside a sentence, use a placeholder and build the markup in code:
`t('Open {link} to review it', {link: `<a href="${hash('review')}">${html(t('Publish'))}</a>`})`.
Escape every user or data value with `html()` before it reaches a placeholder.
Attribute text (`title=`, `aria-label=`, `placeholder=`) is translated the same way and then escaped.

## Glossary

The glossary heads `zh-CN.server.js`. Use its terms in every file, for example evidence link 证据链接,
concept catalog 概念目录, semantic layer 语义层, release 发布版本, passage 段落 and restricted reader 受限读者.
Add a term there before using a new one.
