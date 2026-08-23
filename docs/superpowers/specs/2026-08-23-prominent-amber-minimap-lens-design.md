# Prominent Amber Minimap Lens Design

## Goal

Make the Ridgeline minimap viewport lens immediately recognizable over both
bright sky and dark terrain while preserving its existing glass character.

## Approved treatment

Keep the current lens geometry, translucent vertical-gradient fill, dark outer
separation stroke, and ivory top highlight. Replace the faint one-pixel ivory
rim with a `2.dp` (`2f * density` canvas pixels) `RidgelineTheme.elev` amber rim
at exactly `0.75f` opacity. The rim is static: it does not glow, pulse, or imply
that the lens is an interactive control.

The amber rim is drawn after the glass fill and before the top/bottom highlights
so the highlights remain visible. Draw it fully inset: offset its rectangle by
half the rim width on every side, reduce its width and height by the full rim
width, and reduce its corner radius by half the rim width. The existing dark
stroke remains centered on its current rectangle one pixel outside the lens, so
it is not covered and continues separating amber from terrain at either
luminance extreme.

Expose the production-used constants through an internal `ViewportLensStyle`
value containing `rimColor`, `rimAlpha`, and `rimWidthDp`. The Canvas converts
`rimWidthDp` through its density and uses those same values for the inset rim;
the focused JVM test asserts `RidgelineTheme.elev`, `0.75f`, and `2f` against
that production value.

## Scope

This changes only the lens edge styling. It does not change viewport math,
minimap dimensions, leader lines, route colors, the existing windowed-route
visibility rule (`route.total > POS_WINDOW * 1.12`, currently greater than 672
seconds), or behavior on short routes.

## Verification

Add a pure style assertion for the amber color, opacity, and rim width, then run
the viewport-lens and full Android unit suites plus the debug APK build. Install
on the Galaxy SM-X115 and capture the same 900-second route used for the design
review. Confirm the rim remains distinct over the minimap's light and dark
cells, and restore the device's server preference byte-for-byte afterward.
