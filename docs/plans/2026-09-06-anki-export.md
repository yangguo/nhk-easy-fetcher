# Anki export design (Stage 6)

## Scope

`nhk-easy export-anki` converts **already-fetched** complete articles from the local output tree into an Anki-importable `.apkg` deck. It does not fetch NHK pages, translate text, or sync to AnkiWeb.

## Card model

- **Granularity:** one card per paragraph (matches `ArticleRecord.paragraphs` from the parser).
- **Front:** Japanese text with readings when `--furigana readings` (default); plain kanji/kana when `--furigana plain`.
- **Back:** plain paragraph text plus article id, paragraph index, source URL, and the standard personal-use notice. No English translations are generated.
- **Audio:** when `--include-audio` is set, an existing sibling `audio.m4a` or `audio.mp3` is embedded on the first paragraph card only. Missing audio is skipped; nothing is re-downloaded.

## Output location

Decks are written under the user output tree:

```text
{output}/.nhk-easy-fetcher/anki/{deck-name}.apkg
```

Passing a path ending in `.apkg` to `--output` writes directly to that file. APKG files are gitignored and must not be uploaded as CI artifacts.

## Dependencies

APKG generation uses [`genanki`](https://github.com/kerrickstaley/genanki) behind the optional `[anki]` extra (`pip install -e '.[anki]'`). The library is stable and widely used; TSV export was not implemented because genanki keeps the install footprint small while producing import-ready decks with optional media.

## Copyright boundary

Deck descriptions and card backs repeat: **NHK content — personal study only, do not redistribute.** Users must not publish decks containing NHK article text or audio.
