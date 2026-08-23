# Prominent Amber Minimap Lens Design

## Goal

Make the Ridgeline minimap viewport lens immediately recognizable over both
bright sky and dark terrain while preserving its existing glass character.

## Approved treatment

Keep the current lens geometry, translucent vertical-gradient fill, dark outer
separation stroke, and ivory top highlight. Replace the faint one-pixel ivory
rim with a two-density-pixel `RidgelineTheme.elev` amber rim at approximately
75% opacity. The rim is static: it does not glow, pulse, or imply that the lens
is an interactive control.

The amber rim is drawn after the glass fill and before the top/bottom highlights
so the highlights remain visible. The existing dark stroke stays just outside
the lens, separating amber from terrain at either luminance extreme.

## Scope

This changes only the lens edge styling. It does not change viewport math,
minimap dimensions, leader lines, route colors, the greater-than-600-second
visibility rule, or behavior on short routes.

## Verification

Add a pure style assertion for the amber color, opacity, and rim width, then run
the viewport-lens and full Android unit suites plus the debug APK build. Install
on the Galaxy SM-X115 and capture the same 900-second route used for the design
review. Confirm the rim remains distinct over the minimap's light and dark
cells, and restore the device's server preference byte-for-byte afterward.
