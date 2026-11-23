# Clippa brand

Night Watch direction. The locked logo is the solid filled In-Out C, in a single color.

## Mark colors

- On screen, fill the mark with `#5EA88A` (the Night Watch OK / approve green).
- For print, specify **PANTONE 556 C** (Solid Coated). The sRGB reference for that ink is `#6FA287`. On-screen hex is only an approximation of a Pantone color. Confirm PANTONE 556 C against a current physical Solid Coated swatch book before any printing.
- Hold / warn amber is `#D4A054`. It is a status color, not a fill for the mark.
- Monochrome: use the black (`#000000`) or white (`#FFFFFF`) mark when a single ink is required.

## Night Watch palette

Light mode uses deeper semantic hues, not a simple invert of the dark palette.

| Role | Dark | Light |
| --- | --- | --- |
| Background | `#0B1220` | `#F4F6FA` |
| Surface | `#141C2B` | `#E6EAF2` |
| Text | `#E8EEF6` | `#121826` |
| Muted | `#8B9BB4` | `#4E5C73` |
| Accent | `#7EB8C9` | `#1F6F82` |
| OK / approve | `#5EA88A` | `#1F6B52` |
| Hold / warn | `#D4A054` | `#8A5A12` |
| Finding / error | `#D07070` | `#9B2E2E` |
| Pending / info | `#6B8FD4` | `#1E5A8A` |
| Ink on fills | `#0B1220` | `#FFFFFF` |
| Border | `#2A3548` | `#C5CDD8` |

`#5EA88A` and `#D4A054` were mixed for dark UI. Both pass 3:1 non-text contrast on the dark background and surface, and both fail 3:1 on the light paper and surface.

## UI tokens

Review tools and the war room take these values from [tokens/nightwatch.css](tokens/nightwatch.css) (CSS custom properties) and [tokens/nightwatch.json](tokens/nightwatch.json) (the same values as JSON). Dark is the primary theme.

Approve must always have an icon and a label, because the logo color and the approve color are both `#5EA88A`.

## Voice and tagline

The main tagline is "Clippa keeps watch. You keep the final cut." The secondary is "Clippa stays up. You stay in charge." The full guide is [VOICE.md](VOICE.md).

Never use "we". Clippa is a tool, not a team.

## Type

- UI and wordmark: **IBM Plex Sans**, medium (weight 500), from Google Fonts. The wordmark is lowercase `clippa` with `0.04em` tracking. Do not stretch, outline, or set it in another face or in uppercase.
- Timecodes, scores, and grace timers: **IBM Plex Mono**.

The horizontal and stacked lockup SVGs have the wordmark converted to outlines from IBM Plex Sans Medium, so those files render without the font installed. Live UI and any new lockup still need IBM Plex Sans Medium.

## Lockups, clear space, and size

- Horizontal is the default: the 32-unit mark art box, then a gap equal to the spine width of the C (the vertical bar: 4 units in that 32-unit box), then the wordmark. Stacked uses the same gap under the mark, left-aligned, for tall or narrow slots.
- The color lockups in this kit fill the mark with `#5EA88A` and the wordmark with dark ink `#0B1220`, for use on light backgrounds. On dark backgrounds, keep the mark at `#5EA88A` and recolor the wordmark to text `#E8EEF6`.
- Clear space on every side of the mark equals that same spine width (4 units, or 12.5% of the art box). Nothing else enters that box.
- Minimum mark size is **16 px**. The in/out gap must stay open.

The contained-tile app icon is the filled mark in `#5EA88A`, inset on a rounded square of Night Watch navy `#0B1220` (`rx` 7 in the 32-unit box). Use it for the app icon and the Discord bot avatar.

## Do

- Use the solid filled In-Out C in a single color.
- Use `#5EA88A` on screen and PANTONE 556 C in print, after a swatch-book check.
- Use the navy contained tile for icons and the bot avatar.
- Keep square terminals, the original gap, and the 32-unit art box.
- Use mono black or white when color is not available.
- On light UI, sit the mid-tone seafoam mark on a dark tile, or use mono black, if you need 3:1 graphics contrast.

## Don’t

- Don’t use the retired bold outline, or any two-color in/out treatment, as the master mark.
- Don’t fill the mark with hold amber, accent cyan, finding red, or pending blue.
- Don’t stretch, rotate, outline, or add shadows or gradients to the mark.
- Don’t close or widen the in/out gap, or round the terminals.
- Don’t set `clippa` in another typeface, in uppercase, or with different tracking.
- Don’t use an on-screen hex as a substitute for a physical Pantone chip when printing.
- Don’t tie the mark or the name to BYVFX.
