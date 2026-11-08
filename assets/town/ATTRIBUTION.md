# Superagent Towns — Asset Attribution

`assets/town/` vendors twenty-seven environment PNGs and eight downsampled
character/creature spritesheets under `assets/town/pets/`. They come from
upstream sources under the licences listed below, together with their exact
provenance.

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

## 3. Buildings, trees, props and terrain — Hermes Town (MIT)

`house-router.png`, `house-a.png`, `house-b.png`, `house-c.png`,
`house-d.png`, `house-e.png`, `house-f.png`, `house-g.png`, `tree-a.png`,
`tree-b.png`, `tree-c.png`, `bush.png`, `fountain.png`, `rock.png`, `lamp.png`,
`bridge.png`, `bench.png`, `flowers.png`, `signpost.png`, `well.png`,
`bulletin-board.png`, `road-h.png`, `road-v.png`, `plaza-stone.png` and
`water.png` are derived from the original sprite atlases and procedural terrain
generator in **Hermes Town**, <https://github.com/sxuff/hermes-town>, by the
Hermes Town contributors, or authored for this project under the same MIT
licence.

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

Each extracted file below is a crop of one authored rectangle from the named
atlas. The extraction scripts (`scripts/extract_hermes_town_assets.py` and
`scripts/generate_town_and_pet_assets.py`) remove the reserved magenta chroma
key, trim fully transparent margins, downsample with Lanczos to the scene's
display size, and snap partial alpha to a hard pixel edge. The tileable terrain, well, and bulletin board sprites (`road-h.png`, `road-v.png`,
`plaza-stone.png`, `water.png`, `well.png`, `bulletin-board.png`) are generated
deterministically by `scripts/generate_town_and_pet_assets.py` under the MIT
License. The source atlases are not vendored.

| File | Source atlas / generator | Source rectangle | Output |
| --- | --- | --- | --- |
| `house-router.png` | `buildings.png` (hall) | `(444, 0, 443, 480)` | 190×207 |
| `house-a.png` | `buildings.png` (house) | `(1331, 481, 443, 406)` | 138×147 |
| `house-b.png` | `buildings.png` (tavern) | `(444, 481, 443, 406)` | 138×121 |
| `house-c.png` | `buildings.png` (workshop) | `(887, 0, 444, 478)` | 138×119 |
| `house-d.png` | `buildings.png` (forge) | `(887, 548, 444, 339)` | 138×104 |
| `house-e.png` | `buildings.png` (library) | `(0, 0, 444, 480)` | 138×127 |
| `house-f.png` | `buildings.png` (post) | `(1331, 0, 443, 480)` | 138×129 |
| `house-g.png` | `buildings.png` (observatory) | `(0, 481, 444, 406)` | 138×155 |
| `tree-a.png` | `vegetation.png` | `(0, 0, 525, 518)` | 84×84 |
| `tree-b.png` | `vegetation.png` | `(540, 0, 490, 518)` | 76×82 |
| `tree-c.png` | `vegetation.png` | `(1100, 0, 436, 518)` | 56×93 |
| `bush.png` | `vegetation.png` | `(1050, 600, 486, 424)` | 48×30 |
| `fountain.png` | `props.png` (cell 0) | `(0, 0, 444, 444)` | 82×79 |
| `bridge.png` | `props.png` (cell 4) | `(0, 444, 444, 443)` | 56×36 |
| `rock.png` | `props.png` (cell 5) | `(443, 444, 443, 443)` | 25×19 |
| `lamp.png` | `props.png` (cell 6) | `(887, 444, 443, 443)` | 22×94 |
| `bench.png` | `furniture.png` (cell 0) | `(0, 0, 444, 256)` | 40×24 |
| `flowers.png` | `furniture.png` (cell 1) | `(444, 0, 443, 256)` | 36×24 |
| `signpost.png` | `furniture.png` (cell 5) | `(444, 256, 443, 256)` | 28×38 |
| `well.png` | `generate_town_and_pet_assets.py` | procedural MIT pixel art | 44×48 |
| `bulletin-board.png` | `generate_town_and_pet_assets.py` | procedural MIT pixel art | 48×44 |
| `road-h.png` | `generate_town_and_pet_assets.py` | procedural MIT tile | 64×32 |
| `road-v.png` | `generate_town_and_pet_assets.py` | procedural MIT tile | 32×64 |
| `plaza-stone.png` | `generate_town_and_pet_assets.py` | procedural MIT tile | 64×64 |
| `water.png` | `generate_town_and_pet_assets.py` | procedural MIT tile | 64×64 |

The Hermes Town MIT notice above covers these twenty-five files.

---

## 4. Character & Creature Spritesheets — Petdex Community (`assets/town/pets/`)

`assets/town/pets/` vendors eight pre-downsampled `384×468` WebP spritesheets
(`8×9` frames of `48×52` pixels) together with `catalog.json`, sourced from the
public [Petdex](https://petdex.dev) community manifest
(`https://petdex.dev/api/manifest`):

| File | Display Name | Kind | Submitted By |
| --- | --- | --- | --- |
| `hermes-kody.webp` | Hermes Kody | creature | Calvin N. |
| `coding-cat-pet.webp` | Coding Cat | creature | jiantao y. |
| `ai-workbot.webp` | AI Workbot | character | jhao |
| `knight-shiba.webp` | Knight Shiba | creature | ibreeze2017 |
| `wizard.webp` | Wizard | character | Wu W. |
| `green-slime.webp` | Green Slime | creature | nergy101 |
| `cyberknight.webp` | CyberKnight | character | jddd-hy |
| `peri-the-owl.webp` | Peri the Owl | creature | asyncsan |

---

## Scope

- `grass.png` is **CC-BY-SA 3.0** and carries the attribution and share-alike
  obligations in section 1.
- `person.png` is **CC0 1.0**.
- The twenty-five files listed in section 3 are **MIT**, under the notice in
  that section.
- The community pet spritesheets in `assets/town/pets/` are credited to their
  respective creators in section 4 and `assets/town/pets/catalog.json`.
- None of these images is automatically covered by the Superagent plugin's own
  source licence merely because it lives in this repository. The plugin source
  is MIT-licensed; see the repository-level `LICENSE`.
