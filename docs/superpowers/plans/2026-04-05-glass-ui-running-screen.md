# Glass UI Running Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the solid dark background on the Android Running screen with a full-bleed nature photo, converting all UI panels to tinted glass.

**Architecture:** A bundled JPEG background image fills the Running screen. A `GlassPanel` modifier replaces solid card backgrounds with tinted, low-blur glass. Image brightness is sampled once on load to auto-tune glass parameters (tint opacity, blur, border). All other screens stay unchanged.

**Tech Stack:** Jetpack Compose, `Modifier.blur()` (API 31+), Canvas bitmap sampling, existing PrecorColors theme.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `ui/theme/GlassTheme.kt` | Create | `GlassParams` data class, `glassPanel()` modifier, brightness sampling function |
| `ui/screens/running/RunningScreen.kt` | Modify | Add background image + overlay, pass glass params to children |
| `ui/screens/running/SpeedInclineControls.kt` | Modify | Replace solid card bg with glass modifier |
| `ui/screens/running/ProgramHUD.kt` | Modify | Replace solid card bg with glass modifier |
| `ui/screens/running/BottomBar.kt` | Modify | Replace solid button bg with glass-tinted variant |
| `res/drawable-nodpi/bg_forest.jpg` | Create | Bundled background photo (~300KB JPEG) |

---

### Task 1: Download and bundle the background image

**Files:**
- Create: `kotlin/app/src/main/res/drawable-nodpi/bg_forest.jpg`

- [ ] **Step 1: Download a forest canopy photo**

```bash
curl -L "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=1920&q=80" \
  -o kotlin/app/src/main/res/drawable-nodpi/bg_forest.jpg
```

Verify size is reasonable (~200-400KB). The `drawable-nodpi` directory prevents Android from scaling it.

- [ ] **Step 2: Verify the file exists and is a valid JPEG**

```bash
file kotlin/app/src/main/res/drawable-nodpi/bg_forest.jpg
ls -lh kotlin/app/src/main/res/drawable-nodpi/bg_forest.jpg
```

Expected: JPEG image, 200-400KB.

- [ ] **Step 3: Commit**

```bash
git add kotlin/app/src/main/res/drawable-nodpi/bg_forest.jpg
git commit -m "asset: add forest canopy background photo for glass UI"
```

---

### Task 2: Create GlassTheme — params, modifier, and brightness sampling

**Files:**
- Create: `kotlin/app/src/main/java/com/precor/treadmill/ui/theme/GlassTheme.kt`

- [ ] **Step 1: Create the GlassParams data class and sampling function**

Create `kotlin/app/src/main/java/com/precor/treadmill/ui/theme/GlassTheme.kt`:

```kotlin
package com.precor.treadmill.ui.theme

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Build
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

/**
 * Glass panel parameters derived from background image brightness.
 * Tint opacity is the primary readability lever (not blur).
 * Based on WCAG contrast research and Apple's materials system approach.
 */
data class GlassParams(
    val blur: Dp = 2.dp,
    val panelOpacity: Float = 0.34f,
    val borderOpacity: Float = 0.30f,
    val overlayOpacity: Float = 0.12f,
) {
    companion object {
        /** Default params for when sampling fails or image isn't loaded yet. */
        val Default = GlassParams()

        /** Derive glass parameters from average image brightness (0-255). */
        fun fromBrightness(brightness: Float): GlassParams {
            val b = brightness.coerceIn(0f, 255f)
            return GlassParams(
                blur = (b * 0.012f).coerceIn(0f, 3f).dp,
                panelOpacity = (b * 0.25f).coerceIn(18f, 48f) / 100f,
                borderOpacity = (45f - b * 0.12f).coerceIn(15f, 35f) / 100f,
                overlayOpacity = ((b - 60f) * 0.15f).coerceIn(3f, 25f) / 100f,
            )
        }

        /** Sample average brightness from a drawable resource. */
        fun sampleBrightness(bitmap: Bitmap): Float {
            val scaled = Bitmap.createScaledBitmap(bitmap, 200, 125, true)
            val pixels = IntArray(scaled.width * scaled.height)
            scaled.getPixels(pixels, 0, scaled.width, 0, 0, scaled.width, scaled.height)
            if (scaled !== bitmap) scaled.recycle()

            var totalLum = 0.0
            for (pixel in pixels) {
                val r = (pixel shr 16) and 0xFF
                val g = (pixel shr 8) and 0xFF
                val b2 = pixel and 0xFF
                totalLum += 0.299 * r + 0.587 * g + 0.114 * b2
            }
            return (totalLum / pixels.size).toFloat()
        }
    }
}

/** Remember glass params derived from a drawable resource ID. */
@Composable
fun rememberGlassParams(drawableRes: Int): GlassParams {
    val context = LocalContext.current
    return remember(drawableRes) {
        try {
            val opts = BitmapFactory.Options().apply { inSampleSize = 8 }
            val bitmap = BitmapFactory.decodeResource(context.resources, drawableRes, opts)
                ?: return@remember GlassParams.Default
            val brightness = GlassParams.sampleBrightness(bitmap)
            bitmap.recycle()
            GlassParams.fromBrightness(brightness)
        } catch (_: Exception) {
            GlassParams.Default
        }
    }
}

/** Apply glass panel styling. Use on any composable that should look like tinted glass. */
fun Modifier.glassPanel(
    params: GlassParams,
    shape: RoundedCornerShape = RoundedCornerShape(12.dp),
): Modifier {
    var m = this
        .background(Color.Black.copy(alpha = params.panelOpacity), shape)
        .border(1.dp, Color.White.copy(alpha = params.borderOpacity), shape)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        m = m.blur(params.blur)
    }
    return m
}

/** Glass panel with a custom tint color (e.g., red for stop button). */
fun Modifier.glassPanelTinted(
    params: GlassParams,
    tint: Color,
    tintAlpha: Float = 0.8f,
    shape: RoundedCornerShape = RoundedCornerShape(14.dp),
): Modifier {
    var m = this
        .background(tint.copy(alpha = params.panelOpacity * tintAlpha), shape)
        .border(1.dp, tint.copy(alpha = params.borderOpacity + 0.15f), shape)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        m = m.blur(params.blur)
    }
    return m
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd kotlin && ./gradlew compileDebugKotlin 2>&1 | tail -5
```

Expected: BUILD SUCCESSFUL

- [ ] **Step 3: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/theme/GlassTheme.kt
git commit -m "feat: add GlassTheme with brightness sampling and glass modifiers"
```

---

### Task 3: Add background image and overlay to RunningScreen

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RunningScreen.kt`

- [ ] **Step 1: Add imports at the top of RunningScreen.kt**

Add these imports (after the existing imports):

```kotlin
import androidx.compose.foundation.Image
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import com.precor.treadmill.R
import com.precor.treadmill.ui.theme.rememberGlassParams
import com.precor.treadmill.ui.theme.GlassParams
```

Note: `Image` may conflict with existing imports. Use the fully qualified `androidx.compose.foundation.Image` if needed, or check which `Image` is already imported.

- [ ] **Step 2: Add glass params and background to RunningScreenLandscape**

In `RunningScreenLandscape` (line ~313), inside `BoxWithConstraints`, replace the background modifier and add the image layer.

Change line 331:
```kotlin
.background(Color(0xFF121210)),
```
to:
```kotlin
.background(Color.Black),
```

Then, right after the opening of `BoxWithConstraints` content (before the `val h = maxHeight.value` line), add:

```kotlin
        val glassParams = rememberGlassParams(R.drawable.bg_forest)

        // Background image
        androidx.compose.foundation.Image(
            painter = painterResource(R.drawable.bg_forest),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )

        // Overlay gradient for readability at top/bottom
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    brush = androidx.compose.ui.graphics.Brush.verticalGradient(
                        colors = listOf(
                            Color.Black.copy(alpha = glassParams.overlayOpacity + 0.05f),
                            Color.Black.copy(alpha = glassParams.overlayOpacity / 2f),
                            Color.Black.copy(alpha = glassParams.overlayOpacity / 2f),
                            Color.Black.copy(alpha = glassParams.overlayOpacity + 0.08f),
                        ),
                    ),
                ),
        )
```

- [ ] **Step 3: Pass glassParams down to child composables**

Add `glassParams` parameter to the `Column` content. We'll thread it to children in subsequent tasks. For now, store it in a `CompositionLocalProvider` or pass directly. The simplest approach: add a `CompositionLocal`.

In `GlassTheme.kt`, add at the top level:

```kotlin
import androidx.compose.runtime.compositionLocalOf

val LocalGlassParams = compositionLocalOf { GlassParams.Default }
```

Then in `RunningScreenLandscape`, wrap the `Column` with:

```kotlin
        androidx.compose.runtime.CompositionLocalProvider(
            LocalGlassParams provides glassParams,
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(top = 0.dp, bottom = EdgePad),
            ) {
                // ... existing content unchanged
            }
        }
```

- [ ] **Step 4: Do the same for portrait layout**

Apply identical changes to the portrait `RunningScreen` composable (line ~142): replace `Color(0xFF121210)` with `Color.Black`, add image + overlay + CompositionLocalProvider wrapping.

- [ ] **Step 5: Add text shadow to timer**

Find the timer `Text` in both landscape (~line 398) and portrait. Add a shadow:

```kotlin
style = TextStyle(
    color = Color(0xFFE8E4DF),
    fontSize = timerFontSize,
    fontWeight = FontWeight.SemiBold,
    fontFamily = TimerFontFamily,
    lineHeight = timerFontSize,
    letterSpacing = (-0.03).em,
    fontFeatureSettings = "tnum",
    shadow = Shadow(
        color = Color.Black.copy(alpha = 0.5f),
        offset = Offset(0f, 2f),
        blurRadius = 12f,
    ),
),
```

Add import: `import androidx.compose.ui.geometry.Offset` and `import androidx.compose.ui.graphics.Shadow`

- [ ] **Step 6: Build and verify**

```bash
cd kotlin && ./gradlew assembleDebug 2>&1 | tail -5
```

Expected: BUILD SUCCESSFUL

- [ ] **Step 7: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RunningScreen.kt
git add kotlin/app/src/main/java/com/precor/treadmill/ui/theme/GlassTheme.kt
git commit -m "feat: add background image and overlay to running screen"
```

---

### Task 4: Convert ProgramHUD to glass

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/ProgramHUD.kt`

- [ ] **Step 1: Add imports**

```kotlin
import com.precor.treadmill.ui.theme.LocalGlassParams
import com.precor.treadmill.ui.theme.glassPanel
```

- [ ] **Step 2: Replace card background at line 129**

Find the card background (line ~129):
```kotlin
Color(0xFF1E1D1B)
```
with shape `RoundedCornerShape(16.dp)`.

Replace the `.background(...)` and `.border(...)` modifiers on that Box/Column with:
```kotlin
.glassPanel(LocalGlassParams.current, RoundedCornerShape(16.dp))
```

Remove the existing separate `.border()` call if present (lines ~132-136) since `glassPanel` includes the border.

- [ ] **Step 3: Replace position counter overlay at line 250**

Change:
```kotlin
Color(0xFF1E1D1B).copy(alpha = 0.6f)
```
to:
```kotlin
Color.Black.copy(alpha = LocalGlassParams.current.panelOpacity)
```

- [ ] **Step 4: Build and verify**

```bash
cd kotlin && ./gradlew assembleDebug 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/ProgramHUD.kt
git commit -m "feat: convert ProgramHUD to glass panel"
```

---

### Task 5: Convert SpeedInclineControls to glass

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/SpeedInclineControls.kt`

- [ ] **Step 1: Add imports**

```kotlin
import com.precor.treadmill.ui.theme.LocalGlassParams
import com.precor.treadmill.ui.theme.glassPanel
```

- [ ] **Step 2: Replace ControlPanel background at line 140**

Find:
```kotlin
.background(Color(0xFF1E1D1B), RoundedCornerShape(16.dp))
```

Replace with:
```kotlin
.glassPanel(LocalGlassParams.current, RoundedCornerShape(16.dp))
```

Remove the separate `.border()` modifier on lines ~143-147 since `glassPanel` includes the border.

- [ ] **Step 3: Build and verify**

```bash
cd kotlin && ./gradlew assembleDebug 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/SpeedInclineControls.kt
git commit -m "feat: convert SpeedInclineControls to glass panel"
```

---

### Task 6: Convert BottomBar to glass-tinted buttons

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/BottomBar.kt`

- [ ] **Step 1: Add imports**

```kotlin
import com.precor.treadmill.ui.theme.LocalGlassParams
import com.precor.treadmill.ui.theme.glassPanelTinted
```

- [ ] **Step 2: Replace Resume button background (line ~73)**

Change the green background:
```kotlin
.background(Color(0xFF6BC89B), RoundedCornerShape(14.dp))
```
to:
```kotlin
.glassPanelTinted(LocalGlassParams.current, Color(0xFF6BC89B), shape = RoundedCornerShape(14.dp))
```

- [ ] **Step 3: Replace Reset button background (line ~89)**

Change:
```kotlin
.background(Color(0xFFC45C52).copy(alpha = 0.15f), RoundedCornerShape(14.dp))
```
to:
```kotlin
.glassPanelTinted(LocalGlassParams.current, Color(0xFFC45C52), tintAlpha = 0.4f, shape = RoundedCornerShape(14.dp))
```

- [ ] **Step 4: Replace Stop button background (line ~110)**

Change the stop button background to glass-tinted red:
```kotlin
.glassPanelTinted(LocalGlassParams.current, Color(0xFFC45C52), shape = RoundedCornerShape(14.dp))
```

- [ ] **Step 5: Build and verify**

```bash
cd kotlin && ./gradlew assembleDebug 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/BottomBar.kt
git commit -m "feat: convert BottomBar buttons to glass-tinted panels"
```

---

### Task 7: Convert MetricsRow to glass strip

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/MetricsRow.kt`

- [ ] **Step 1: Add imports**

```kotlin
import com.precor.treadmill.ui.theme.LocalGlassParams
import com.precor.treadmill.ui.theme.glassPanel
```

- [ ] **Step 2: Wrap the metrics Row in a glass panel**

Find the `Row` composable that holds the metrics. Add the glass modifier to it:

```kotlin
.glassPanel(LocalGlassParams.current, RoundedCornerShape(10.dp))
```

If the Row doesn't currently have a background, add the modifier. If it does, replace the existing background.

- [ ] **Step 3: Add text shadows to metric values**

Add `shadow` to the metric text styles so they're readable over the glass:

```kotlin
style = TextStyle(
    // ... existing style properties ...
    shadow = Shadow(
        color = Color.Black.copy(alpha = 0.4f),
        offset = Offset(0f, 1f),
        blurRadius = 4f,
    ),
)
```

- [ ] **Step 4: Build and verify**

```bash
cd kotlin && ./gradlew assembleDebug 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/MetricsRow.kt
git commit -m "feat: convert MetricsRow to glass strip with text shadows"
```

---

### Task 8: Build, deploy to tablet, and visual verification

**Files:** None (verification only)

- [ ] **Step 1: Full build**

```bash
cd kotlin && ./gradlew assembleDebug 2>&1 | tail -5
```

- [ ] **Step 2: Install on tablet**

```bash
adb -s adb-R9ZY90P5LZP-WMXOYu._adb-tls-connect._tcp install -r \
  kotlin/app/build/outputs/apk/debug/app-debug.apk
```

- [ ] **Step 3: Visual verification checklist**

Open the app on the tablet and verify:
- [ ] Background photo visible behind all panels in landscape running screen
- [ ] Background photo visible behind all panels in portrait running screen
- [ ] Timer text readable with shadow
- [ ] Metrics strip is glass with readable text
- [ ] Elevation HUD is glass — photo visible through it
- [ ] Speed/incline controls are glass
- [ ] Stop bar is red-tinted glass
- [ ] Resume/Reset buttons are tinted glass when paused
- [ ] Lobby screen is unchanged (solid dark background)
- [ ] No performance issues (smooth scrolling, no jank)

- [ ] **Step 4: Commit all if any fixups were needed**

```bash
git add -A && git commit -m "fix: glass UI visual polish from tablet verification"
```
