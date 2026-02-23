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
import androidx.compose.ui.platform.LocalConfiguration
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel

@Composable
fun BottomBar(
    viewModel: TreadmillViewModel,
    showControls: Boolean = true,
    modifier: Modifier = Modifier,
) {
    val status by viewModel.status.collectAsState()
    val pgm by viewModel.derivedProgram.collectAsState()
    val context = LocalContext.current

    val isRunning = status.emulate && (status.emuSpeed > 0 || (pgm.running && !pgm.paused))
    val btnHeight = if (LocalConfiguration.current.screenWidthDp >= 600) 58.dp else 50.dp

    // Use safeDrawing insets for bottom — covers nav bar, display cutouts, and curved screens
    val bottomSafe = WindowInsets.safeDrawing.asPaddingValues().calculateBottomPadding()
    val bottomPad = max(bottomSafe, 8.dp)

    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(top = 6.dp)
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
                        containerColor = Color(0xFF6BC89B),
                        contentColor = Color.White,
                    ),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier
                        .weight(2f)
                        .height(btnHeight),
                ) {
                    Text("Resume", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                }
                Button(
                    onClick = {
                        viewModel.resetAll()
                        haptic(context, longArrayOf(50, 30, 50))
                    },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color(0xFFC45C52).copy(alpha = 0.15f),
                        contentColor = Color(0xFFC45C52),
                    ),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier
                        .weight(1f)
                        .height(btnHeight),
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
                        containerColor = if (isRunning) Color(0xFFC45C52) else Color(0x3D787880),
                        contentColor = if (isRunning) Color.White else Color(0x59E8E4DF),
                        disabledContainerColor = Color(0x3D787880),
                        disabledContentColor = Color(0x59E8E4DF),
                    ),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier
                        .weight(1f)
                        .height(btnHeight)
                        .alpha(if (isRunning) 1f else 0.4f),
                ) {
                    Text("Stop", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}
