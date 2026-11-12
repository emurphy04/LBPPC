# Translating the LBPPC launcher

The launcher follows the game's language (its Language setting, or Windows' when Automatic). Each language is one
file in `source/`, which you can edit in any text editor:

| File | Language |
|---|---|
| `fr.txt` | French |
| `es.txt` | Spanish (Spain and Latin America) |
| `de.txt` | German |
| `it.txt` | Italian |
| `nl.txt` | Dutch |
| `pt.txt` | Portuguese (Portugal) |
| `pt-BR.txt` | Portuguese (Brazil) |
| `ru.txt` | Russian |
| `fi.txt` | Finnish |
| `sv.txt` | Swedish |
| `da.txt` | Danish |
| `nb.txt` | Norwegian (Bokmål) |
| `pl.txt` | Polish |


## The format

Each line is `number|text`. The number says which English text it is: line `84` is the English text number 84 in
`strings.json` (the 85th, counting from 0). For example:

```
84|Jouer
```

- Keep everything in `{braces}` exactly as it is, such as `{name}` or `{version}`: the launcher puts the real value
  there. Move it wherever your sentence needs it.
- `\n` is a line break.
- A few texts start with a space (` and {count} more`): keep the space.
- Lines starting with `#` are notes and are ignored.
- Button names appear inside other texts too ("click Set up..."): use the same words in both places.

The first drafts were written by machine and need a fluent speaker's eye. Fix anything that reads wrong, sounds
unnatural, or doesn't match what LittleBigPlanet players actually call things.

## Checking your changes

```
python launcher/lang/build.py
```

It builds the `<code>.json` file the launcher reads, and says if a line has a `{field}` missing or changed, or if a
text has no translation (that text then shows in English). Send the `.txt` file, or a pull request, through the Discord.

## For developers

New launcher texts go through `t('English text', field=value)`, or `N_('...')` for lists made before a language is
chosen. `python launcher/i18n.py --extract` adds new texts to the end of `strings.json` (the numbering never changes);
untranslated ones show in English until a translator adds them.
