package com.precor.treadmill.ui.screens.running

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.unit.max
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LegibleGlassPanel
import com.precor.treadmill.ui.theme.touchFingerPad
import com.precor.treadmill.ui.theme.touchThumbPad
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel

@Composable
fun BottomBar(
    viewModel: TreadmillViewModel,
    showControls: Boolean = true,
    externalPadding: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val status by viewModel.status.collectAsState()
    val pgm by viewModel.derivedProgram.collectAsState()
    val context = LocalContext.current

    val isRunning = status.emulate && (status.emuSpeed > 0 || (pgm.running && !pgm.paused))
    val defaultHeight = touchFingerPad()
    val stopHeight = touchThumbPad()

    // Use safeDrawing insets for bottom — covers nav bar, display cutouts, and curved screens
    val bottomSafe = WindowInsets.safeDrawing.asPaddingValues().calculateBottomPadding()
    val bottomPad = if (externalPadding) 0.dp else max(bottomSafe, 8.dp)
    val topPad = if (externalPadding) 0.dp else 6.dp

    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(top = topPad)
            .padding(bottom = bottomPad),
    ) {
        // Speed/Incline controls (hidden in landscape where they're shown separately)
        if (showControls) {
            SpeedInclineControls(
                viewModel = viewModel,
                modifier = Modifier.padding(bottom = 12.dp),
            )
        }

        // Action buttons
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (pgm.paused) {
                // Resume (green) + Reset (red) — each button's brand color IS its scrim: the
                // color's opacity is solved like any scrim so the white label clears APCA over
                // the photo behind it, keeping the button solidly green/red as needed.
                ActionButton(
                    text = "Resume",
                    brand = Color(0xFF6BC89B),
                    onClick = { viewModel.pauseProgram(); haptic(context, 25) },
                    fontSize = 17.sp,
                    modifier = Modifier.weight(2f).height(defaultHeight),
                )
                ActionButton(
                    text = "Reset",
                    brand = Color(0xFFC45C52),
                    onClick = { viewModel.resetAll(); haptic(context, longArrayOf(50, 30, 50)) },
                    fontSize = 15.sp,
                    modifier = Modifier.weight(1f).height(defaultHeight),
                )
            } else {
                // Stop button
                ActionButton(
                    text = "Stop",
                    brand = Color(0xFFC45C52),
                    onClick = { if (isRunning) { viewModel.pauseProgram(); haptic(context, longArrayOf(50, 30, 50)) } },
                    enabled = isRunning,
                    fontSize = 17.sp,
                    modifier = Modifier.weight(1f).height(stopHeight),
                )
            }
        }
    }
}

/**
 * An action button whose brand color is its scrim: [LegibleGlassPanel] raises the color's
 * opacity (the same APCA scrim math) until the white label clears over the photo behind it,
 * so the button reads as a solid green/red surface — as solid as needed, no more.
 */
@Composable
private fun ActionButton(
    text: String,
    brand: Color,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    fontSize: TextUnit = 17.sp,
) {
    LegibleGlassPanel(
        accents = listOf(Color.White),
        scrimColor = brand,
        modifier = modifier.alpha(if (enabled) 1f else 0.4f),
        shape = RoundedCornerShape(14.dp),
        targetLc = 60.0,
        // Floor is high because a button must read as a SOLID tappable surface — a higher bar
        // than mere label legibility. The scrim math raises it further on bright backgrounds.
        minOpacity = 0.82f,
        maxOpacity = 0.96f,
    ) {
        Button(
            onClick = onClick,
            enabled = enabled,
            colors = ButtonDefaults.buttonColors(
                containerColor = Color.Transparent,
                contentColor = Color.White,
                disabledContainerColor = Color.Transparent,
                disabledContentColor = Color.White.copy(alpha = 0.6f),
            ),
            shape = RoundedCornerShape(14.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            Text(text, fontSize = fontSize, fontWeight = FontWeight.SemiBold)
        }
    }
}
