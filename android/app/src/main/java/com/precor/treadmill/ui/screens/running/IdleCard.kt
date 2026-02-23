package com.precor.treadmill.ui.screens.running

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.components.HistoryList
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel

private val motivations = listOf(
    "Let's get moving!",
    "Your legs are ready.",
    "One step at a time.",
    "You showed up. That's the hardest part.",
    "Today's a good day for a run.",
    "Fresh air for the mind.",
    "Just press play.",
    "Your future self says thanks.",
    "Run like nobody's watching.",
    "Miles don't care about Mondays.",
    "Lace up, zone out.",
    "The belt is waiting for you.",
)

@Composable
fun IdleCard(
    viewModel: TreadmillViewModel,
    onVoice: (prompt: String?) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val motivation = remember { motivations.random() }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 8.dp)
            .background(
                color = colors.card,
                shape = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
            )
            .padding(1.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        // Motivation + subtitle — top padding clears the floating icons
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 56.dp, start = 24.dp, end = 24.dp, bottom = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = motivation,
                color = colors.text2,
                fontSize = 22.sp,
                fontWeight = FontWeight.SemiBold,
                textAlign = TextAlign.Center,
                lineHeight = 29.sp,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                text = "Set your speed, touch mic, or pick a program to start",
                color = colors.text3,
                fontSize = 13.sp,
                textAlign = TextAlign.Center,
            )
        }

        // History — scrollable
        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(bottom = 8.dp),
        ) {
            HistoryList(
                variant = "lobby",
                onAfterLoad = {
                    viewModel.startProgram()
                    haptic(context, longArrayOf(25, 30, 25))
                },
            )
        }
    }
}
