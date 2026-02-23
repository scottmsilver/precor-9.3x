package com.precor.treadmill.ui.screens.lobby

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.components.HistoryList
import com.precor.treadmill.ui.components.MiniStatusCard
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.theme.PillShape
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import org.koin.androidx.compose.koinViewModel
import java.util.Calendar

private fun isTablet(widthDp: Int): Boolean = widthDp >= 600

private fun greeting(): String {
    val h = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
    return when {
        h < 12 -> "Good morning"
        h < 17 -> "Good afternoon"
        else -> "Good evening"
    }
}

@Composable
fun LobbyScreen(
    onNavigateToRun: () -> Unit,
    viewModel: TreadmillViewModel = koinViewModel(),
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val session by viewModel.derivedSession.collectAsState()
    val program by viewModel.derivedProgram.collectAsState()
    val configuration = androidx.compose.ui.platform.LocalConfiguration.current
    val tablet = isTablet(configuration.screenWidthDp)

    val workoutActive = session.active || program.running

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.bg)
            .statusBarsPadding(),
    ) {
        // Greeting + action buttons
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .widthIn(max = if (tablet) 640.dp else Dp.Unspecified)
                .padding(top = 8.dp, start = 16.dp, end = 16.dp, bottom = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = greeting(),
                fontSize = if (tablet) 28.sp else 22.sp,
                fontWeight = FontWeight.Bold,
                color = colors.text,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                text = "Ready for a run?",
                fontSize = 14.sp,
                color = colors.text3,
            )
            Spacer(Modifier.height(16.dp))
            Row(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (workoutActive) {
                    LobbyButton(
                        text = "Return to Workout",
                        isPrimary = true,
                        onClick = {
                            onNavigateToRun()
                            haptic(context, 25)
                        },
                    )
                } else {
                    LobbyButton(
                        text = "Quick",
                        isPrimary = false,
                        onClick = {
                            onNavigateToRun()
                            haptic(context, 25)
                        },
                    )
                    LobbyButton(
                        text = "Manual",
                        isPrimary = true,
                        onClick = {
                            viewModel.quickStart()
                            haptic(context, longArrayOf(25, 30, 25))
                            onNavigateToRun()
                        },
                    )
                }
                if (program.program != null && !program.running) {
                    LobbyButton(
                        text = "Start Program",
                        isPrimary = true,
                        onClick = {
                            viewModel.startProgram()
                            haptic(context, longArrayOf(25, 30, 25))
                            onNavigateToRun()
                        },
                    )
                }
            }
        }

        // Mini status card
        MiniStatusCard(onClick = onNavigateToRun)

        // History list
        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState()),
        ) {
            Text(
                text = "YOUR PROGRAMS",
                color = colors.text3,
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 0.3.sp,
                modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 8.dp),
            )
            HistoryList(
                variant = "lobby",
                onAfterLoad = {
                    viewModel.startProgram()
                    haptic(context, longArrayOf(25, 30, 25))
                    onNavigateToRun()
                },
            )
            Spacer(Modifier.height(16.dp))
        }
    }
}

@Composable
fun LobbyButton(
    text: String,
    isPrimary: Boolean,
    onClick: () -> Unit,
) {
    val colors = LocalPrecorColors.current
    val bg = if (isPrimary) colors.green else colors.fill
    val fg = if (isPrimary) colors.bg else colors.text

    val widthDp = androidx.compose.ui.platform.LocalConfiguration.current.screenWidthDp
    val btnHeight = if (isTablet(widthDp)) 56.dp else 48.dp

    androidx.compose.material3.Button(
        onClick = onClick,
        colors = androidx.compose.material3.ButtonDefaults.buttonColors(
            containerColor = bg,
            contentColor = fg,
        ),
        shape = PillShape,
        contentPadding = PaddingValues(horizontal = 24.dp, vertical = 0.dp),
        modifier = Modifier.height(btnHeight),
    ) {
        Text(
            text = text,
            fontSize = if (isTablet(widthDp)) 17.sp else 15.sp,
            fontWeight = if (isPrimary) FontWeight.Bold else FontWeight.SemiBold,
        )
    }
}
