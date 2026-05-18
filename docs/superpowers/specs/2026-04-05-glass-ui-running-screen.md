# Glass UI — Running Screen Background

## Summary

Replace the solid `#121210` background on the Android Running screen with a full-bleed nature photograph. All UI panels (metrics strip, elevation HUD, speed/incline controls, stop bar) become tinted glass sheets floating over the image. Text stays white with text shadows for readability.

**Scope:** Android Running screen landscape layout only. Lobby, settings, and other screens stay unchanged. iOS and web are out of scope for this change.

## Design Parameters (from interactive prototyping)

Glass panels use tint opacity as the primary readability lever, not blur. Based on WCAG contrast research and Apple's materials system approach.

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Blur | 0-3px | Minimal — just softens hard photo edges at panel boundaries |
| Panel opacity | 18-48% black | Main readability lever, scaled by image brightness |
| Border | 15-35% white | Defines glass edges, stronger on darker images |
| Overlay gradient | 3-25% | Top/bottom darkening for timer and stop bar areas |

These values are auto-tuned per image based on sampled brightness.

## Architecture

### Image Brightness Sampling

On first load of a background image, sample its average luminance:
1. Scale image to ~200x125 on a Canvas/Bitmap
2. Compute average luminance: `0.299*R + 0.587*G + 0.114*B` across all pixels
3. Derive glass parameters from the brightness value (0-255):
   - `blur = clamp(brightness * 0.012, 0, 3)`
   - `panelOpacity = clamp(brightness * 0.25, 18, 48)` (percent)
   - `overlay = clamp((brightness - 60) * 0.15, 3, 25)` (percent)
   - `border = clamp(45 - brightness * 0.12, 15, 35)` (percent)
4. Cache the computed parameters alongside the image (no re-sampling needed)

### Background Image

- Bundle one default image in the app (nature/forest theme, landscape orientation)
- Image stored in `res/drawable/` or `res/raw/` as a high-quality JPEG
- Future: user-selectable from a set of bundled options (not in this change)
- Image must be landscape aspect ratio, minimum 1920x1200 for tablet displays
- Use `ContentScale.Crop` to fill the screen regardless of device aspect ratio

### Glass Composable

Create a reusable `GlassPanel` modifier or composable:

```kotlin
fun Modifier.glassPanel(
    panelOpacity: Float,  // 0.0-1.0
    blur: Dp,             // 0-3dp
    borderOpacity: Float, // 0.0-1.0
) = this
    .background(Color.Black.copy(alpha = panelOpacity), shape)
    .blur(blur)  // Modifier.blur on API 31+, fallback to solid on older
    .border(1.dp, Color.White.copy(alpha = borderOpacity), shape)
```

### Running Screen Changes

1. **Background layer**: `Image` composable filling the screen with the bundled photo, behind all UI content
2. **Overlay gradient**: Semi-transparent gradient on top of the image (darker at top/bottom for timer and stop bar readability)
3. **All panels**: Replace solid `Color(0xFF1E1D1B)` backgrounds with `glassPanel()` modifier using the sampled parameters
4. **Timer text**: Add `textShadow` or `shadow` for readability over varied backgrounds
5. **Stop bar**: Uses red-tinted glass (`rgba(196,92,82, opacity*0.8)`) instead of black-tinted

### Panels to Convert

| Panel | Current | Glass |
|-------|---------|-------|
| Metrics strip | solid card color | `glassPanel(opacity, blur, border)` |
| Elevation HUD | solid card color | `glassPanel(opacity, blur, border)` |
| Speed control | solid card color | `glassPanel(opacity, blur, border)` |
| Incline control | solid card color | `glassPanel(opacity, blur, border)` |
| Stop/Resume bar | solid red/green | red/green-tinted glass |
| +/- buttons | `Color.White.copy(0.10f)` | unchanged (already glass-like) |

### API Level Considerations

- `Modifier.blur()` requires API 31+ (Android 12)
- On API < 31: skip blur, increase panel opacity by ~5% to compensate
- Test on both paths

### Portrait Mode

Apply the same treatment to portrait RunningScreen layout. Same image, same glass parameters, different panel arrangement.

## What Doesn't Change

- Lobby screen (stays solid `#121210`)
- Settings panel
- Profile picker
- Navigation bar/rail
- Color palette (green, red, etc. accent colors stay the same)
- Font families and sizes
- Card shapes and corner radii

## Testing

- Visual verification on tablet (landscape) and phone (portrait)
- Verify text readability across bright and dark background images
- Verify API < 31 fallback (no blur, slightly darker panels)
- Verify no performance regression (single static image, no animation)

## Assets Needed

- One high-quality landscape nature photograph (forest/trail theme)
- JPEG, ~1920x1200 minimum, reasonable file size (~200-400KB)
- License: Unsplash or similar royalty-free (attribute in app credits if required)
