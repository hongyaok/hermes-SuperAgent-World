# Superagent Towns — Asset Attribution

The Superagent Towns scene (`assets/town/`) vendors eight pixel-art images. They
come from two upstream OpenGameArt packs, under two different licenses. Both
licenses are reproduced below, together with the exact provenance of every file.

The images in this directory are **not** covered by the repository's source-code
license. See [Scope](#scope-these-licenses-are-separate-from-the-repository-license).

---

## 1. Tileset crops — "16x16 RPG Tileset" by hilau (CC-BY-SA 3.0)

`grass.png`, `house-router.png`, `house-a.png`, `house-b.png` and `house-c.png`
are **cropped derivatives** of the "16x16 RPG Tileset" by
[hilau](https://opengameart.org/users/hilau),
<https://opengameart.org/content/16x16-rpg-tileset>.

That tileset is licensed **Creative Commons Attribution-ShareAlike 3.0 Unported
(CC-BY-SA 3.0)**, <https://creativecommons.org/licenses/by-sa/3.0/>. The upstream
page also offers the pack under GPL 3.0; this project elects **CC-BY-SA 3.0** for
its derivatives.

The tileset is itself based on two earlier packs, which must be credited too:

- "16x16 Game Assets" by **George Bailey**,
  <https://opengameart.org/content/16x16-game-assets> — licensed
  **CC-BY 4.0**, <https://creativecommons.org/licenses/by/4.0/>.
- "LPC Thatched-roof Cottage" by **bluecarrot16**,
  <https://opengameart.org/content/lpc-thatched-roof-cottage>.

### Attribution text requested by hilau

hilau asks that the following text be included in the credits of any project
using the tileset. It is reproduced here verbatim:

> Uses the "16x16 RPG Tileset" by hilau at https://opengameart.org/content/16x16-rpg-tileset, which is based off of "16x16 Game Assets" by George Bailey at https://opengameart.org/content/16x16-game-assets.
> and "LPC Thatched-roof Cottage" by bluecarrot16 at https://opengameart.org/content/lpc-thatched-roof-cottage.

### Crops taken

Each file is a 1:1 pixel crop of `rpg-tileset.png` — no resampling, no scaling,
no padding. Boxes are `(left, top, right, bottom)` in source pixels.

| File | Source | Crop box | Size |
| --- | --- | --- | --- |
| `grass.png` | `rpg-tileset.png` | `(16, 32, 80, 64)` | 64×32 |
| `house-router.png` | `rpg-tileset.png` | `(1048, 18, 1164, 128)` | 116×110 |
| `house-a.png` | `rpg-tileset.png` | `(1288, 24, 1368, 106)` | 80×82 |
| `house-b.png` | `rpg-tileset.png` | `(1384, 24, 1464, 106)` | 80×82 |
| `house-c.png` | `rpg-tileset.png` | `(1480, 24, 1560, 106)` | 80×82 |

These five files are distributed under **CC-BY-SA 3.0**
(<https://creativecommons.org/licenses/by-sa/3.0/>). If you reuse or modify
them, you must credit hilau (and George Bailey and bluecarrot16), link to the
license, indicate that changes were made — they are crops — and license your
derivative under CC-BY-SA 3.0 as well.

---

## 2. Character and trees — "Tiny RPG - Forest" by Luis Zuno (@ansimuz) (CC0 1.0)

`person.png`, `tree-a.png` and `tree-b.png` are unmodified copies from
**"Tiny RPG - Forest"** by **Luis Zuno (@ansimuz)**,
<https://opengameart.org/content/tiny-rpg-forest>.

That pack is dedicated to the public domain under **CC0 1.0 Universal**,
<https://creativecommons.org/publicdomain/zero/1.0/>.

CC0 imposes no attribution requirement — credit is not required, but it is given
here gladly. Upstream credit line: *Artwork created by Luis Zuno @ansimuz.*

| File | Source file in the pack | Size |
| --- | --- | --- |
| `person.png` | `player.png` | 48×64 |
| `tree-a.png` | `nature/Tree2.png` | 64×64 |
| `tree-b.png` | `nature/Tree3.png` | 64×64 |

`person.png` is a 48×64 sheet: three 16×16 walk frames per direction, four
directions.

---

## Scope: these licenses are separate from the repository license

The licenses above apply **only** to the image files in `assets/town/`. They are
separate from, and additional to, the MIT license that governs the Superagent
plugin's source code.

**No file in `assets/town/` is MIT-licensed.** The MIT grant does not extend to
these images, and the CC-BY-SA 3.0 obligations on the five tileset crops survive
any redistribution of this repository.
