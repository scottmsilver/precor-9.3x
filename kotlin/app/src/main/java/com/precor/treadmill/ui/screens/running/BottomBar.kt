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
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LocalGlassParams
import com.precor.treadmill.ui.theme.glassPanelTinted
import com.precor.treadmill.ui.theme.legibleOn
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
                // Resume + Reset
                Button(
                    onClick = {
                        viewModel.pauseProgram()
                        haptic(context, 25)
                    },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color.Transparent,
                        // Guarantee the label contrasts the button's own tint (its identity color).
                        contentColor = Color.White.legibleOn(Color(0xFF6BC89B)),
                    ),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier
                        .weight(2f)
                        .height(defaultHeight)
                        .glassPanelTinted(LocalGlassParams.current, Color(0xFF6BC89B), shape = RoundedCornerShape(14.dp)),
                ) {
                    Text("Resume", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                }
                Button(
                    onClick = {
                        viewModel.resetAll()
                        haptic(context, longArrayOf(50, 30, 50))
                    },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color.Transparent,
                        // Was red text on a red tint (illegible) — nudge it to a legible red.
                        contentColor = Color(0xFFC45C52).legibleOn(Color(0xFFC45C52)),
                    ),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier
                        .weight(1f)
                        .height(defaultHeight)
                        .glassPanelTinted(LocalGlassParams.current, Color(0xFFC45C52), tintAlpha = 0.4f, shape = RoundedCornerShape(14.dp)),
                ) {
                    Text("Reset", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                }
            } else {
                // Stop button
                Button(
                    onClick = {
                        if (isRunning) {
                            viewModel.pauseProgram()
                            haptic(context, longArrayOf(50, 30, 50))
                        }
                    },
                    enabled = isRunning,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color.Transparent,
                        contentColor = Color.White.legibleOn(Color(0xFFC45C52)),
                        disabledContainerColor = Color.Transparent,
                        disabledContentColor = Color(0x59E8E4DF),
                    ),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier
                        .weight(1f)
                        .height(stopHeight)
                        .alpha(if (isRunning) 1f else 0.4f)
                        .glassPanelTinted(LocalGlassParams.current, Color(0xFFC45C52), shape = RoundedCornerShape(14.dp)),
                ) {
                    Text("Stop", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}
