package com.precor.treadmill.ui.screens.running

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel

@Composable
fun ProgramHUD(
    viewModel: TreadmillViewModel,
    modifier: Modifier = Modifier,
) {
    val pgm by viewModel.derivedProgram.collectAsState()

    // Elevation profile card — matches web ProgramHUD layout
    Box(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 6.dp),
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    color = Color(0xFF1E1D1B),
                    shape = RoundedCornerShape(16.dp),
                )
                .border(
                    width = 1.dp,
                    color = Color.White.copy(alpha = 0.25f),
                    shape = RoundedCornerShape(16.dp),
                ),
        ) {
            // Elevation profile fills the card
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(start = 4.dp, end = 4.dp, top = 6.dp, bottom = 2.dp),
            ) {
                ElevationProfile(
                    viewModel = viewModel,
                    onSkip = { viewModel.skipInterval() },
                    onPrev = { viewModel.prevInterval() },
                )
            }

            // Position counter overlay (top-right)
            if (pgm.intervalCount > 1) {
                Box(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(top = 8.dp, end = 10.dp)
                        .background(
                            color = Color(0xFF1E1D1B).copy(alpha = 0.6f),
                            shape = RoundedCornerShape(4.dp),
                        )
                        .padding(horizontal = 8.dp, vertical = 2.dp),
                ) {
                    Text(
                        text = "${pgm.currentInterval + 1} of ${pgm.intervalCount}",
                        color = Color(0x59E8E4DF),
                        fontSize = 11.sp,
                    )
                }
            }
        }
    }
}
