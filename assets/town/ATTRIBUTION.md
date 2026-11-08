# Superagent Towns — Asset Attribution

`assets/town/` vendors fourteen PNGs. They come from three upstream sources
under three different licences. The licence of each file is listed below,
together with its exact provenance.

These image licences are **separate from the licence of the Superagent plugin's
source code**. See [Scope](#scope).

---

## 1. Grass — "16x16 RPG Tileset" by hilau (CC-BY-SA 3.0)

`grass.png` is a 1:1 pixel crop of the "16x16 RPG Tileset" by
[hilau](https://opengameart.org/users/hilau),
<https://opengameart.org/content/16x16-rpg-tileset>.

That tileset is licensed **Creative Commons Attribution-ShareAlike 3.0 Unported
(CC-BY-SA 3.0)**, <https://creativecommons.org/licenses/by-sa/3.0/>. The
upstream page also offers the pack under GPL 3.0; this project elects
**CC-BY-SA 3.0** for this derivative.

The tileset is itself based on two earlier packs, which must be credited too:

- "16x16 Game Assets" by **George Bailey**,
  <https://opengameart.org/content/16x16-game-assets> — licensed
  **CC-BY 4.0**, <https://creativecommons.org/licenses/by/4.0/>.
- "LPC Thatched-roof Cottage" by **bluecarrot16**,
  <https://opengameart.org/content/lpc-thatched-roof-cottage>.

### Attribution text requested by hilau

> Uses the "16x16 RPG Tileset" by hilau at https://opengameart.org/content/16x16-rpg-tileset, which is based off of "16x16 Game Assets" by George Bailey at https://opengameart.org/content/16x16-game-assets.
> and "LPC Thatched-roof Cottage" by bluecarrot16 at https://opengameart.org/content/lpc-thatched-roof-cottage.

### Crop taken

| File | Source | Crop box | Size |
| --- | --- | --- | --- |
| `grass.png` | `rpg-tileset.png` | `(16, 32, 80, 64)` | 64×32 |

This file is distributed under **CC-BY-SA 3.0**. If you reuse or modify it, you
must credit hilau (and George Bailey and bluecarrot16), link to the licence,
indicate that changes were made — it is a crop — and license your derivative
under CC-BY-SA 3.0 as well.

---

## 2. Person — "Tiny RPG - Forest" by Luis Zuno (@ansimuz) (CC0 1.0)

`person.png` is an unmodified copy of `player.png` from **"Tiny RPG - Forest"**
by **Luis Zuno (@ansimuz)**,
<https://opengameart.org/content/tiny-rpg-forest>.

That pack is dedicated to the public domain under **CC0 1.0 Universal**,
<https://creativecommons.org/publicdomain/zero/1.0/>. CC0 imposes no
attribution requirement — credit is not required, but it is given here gladly.
Upstream credit line: *Artwork created by Luis Zuno @ansimuz.*

`person.png` is a 48×64 sheet: three 16×16 walk frames per direction, four
directions.

---

## 3. Buildings, trees and props — Hermes Town (MIT)

`house-router.png`, `house-a.png`, `house-b.png`, `house-c.png`,
`house-d.png`, `tree-a.png`, `tree-b.png`, `tree-c.png`, `bush.png`,
`fountain.png`, `rock.png` and `lamp.png` are derived from the original sprite
atlases in **Hermes Town**, <https://github.com/sxuff/hermes-town>, by the
Hermes Town contributors.

Hermes Town's code and shipped original assets are licensed under the **MIT
License**:

> MIT License
>
> Copyright (c) 2026 Hermes Town contributors
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

### Derivative work

Each file below is a crop of one authored rectangle from the named atlas. The
extraction script (`scripts/extract_hermes_town_assets.py`) removes the
reserved magenta chroma key, trims fully transparent margins, downsamples with
Lanczos to the scene's display size, and snaps partial alpha to a hard pixel
edge. The source atlases are not vendored.

| File | Source atlas | Source rectangle | Output |
| --- | --- | --- | --- |
| `house-router.png` | `buildings.png` (hall) | `(444, 0, 443, 480)` | 190×207 |
| `house-a.png` | `buildings.png` (house) | `(1331, 481, 443, 406)` | 138×147 |
| `house-b.png` | `buildings.png` (tavern) | `(444, 481, 443, 406)` | 138×121 |
| `house-c.png` | `buildings.png` (workshop) | `(887, 0, 444, 478)` | 138×119 |
| `house-d.png` | `buildings.png` (forge) | `(887, 548, 444, 339)` | 138×104 |
| `tree-a.png` | `vegetation.png` | `(0, 0, 525, 518)` | 84×84 |
| `tree-b.png` | `vegetation.png` | `(540, 0, 490, 518)` | 76×82 |
| `tree-c.png` | `vegetation.png` | `(1100, 0, 436, 518)` | 56×93 |
| `bush.png` | `vegetation.png` | `(1050, 600, 486, 424)` | 48×30 |
| `fountain.png` | `props.png` (cell 0) | `(0, 0, 444, 444)` | 82×79 |
| `rock.png` | `props.png` (cell 5) | `(443, 444, 443, 443)` | 25×19 |
| `lamp.png` | `props.png` (cell 6) | `(887, 444, 443, 443)` | 22×94 |

The editing is limited to the transformations above; the underlying artwork is
unchanged. The Hermes Town MIT notice above covers these derivative files.

---

## Scope

- `grass.png` is **CC-BY-SA 3.0** and carries the attribution and share-alike
  obligations in section 1.
- `person.png` is **CC0 1.0**.
- The twelve files listed in section 3 are **MIT**, under the notice in that
  section.
- None of these images is automatically covered by the Superagent plugin's own
  source licence merely because it lives in this repository. The plugin source
  is MIT-licensed; see the repository-level `LICENSE`.
