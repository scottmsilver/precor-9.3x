package com.precor.treadmill.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LegibleText
import com.precor.treadmill.ui.theme.LocalPrecorColors

/**
 * Shared component that shows both My Workouts and Your Programs (history).
 * Use everywhere program lists are shown for consistent behavior.
 *
 * - lobby variant: vertical list with headers, bidirectional refresh
 * - compact variant: delegates to HistoryList compact (horizontal scroll)
 */
@Composable
fun ProgramBrowser(
    variant: String,
    onAfterLoad: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val colors = LocalPrecorColors.current
    var workoutListKey by remember { mutableIntStateOf(0) }
    var historyListKey by remember { mutableIntStateOf(0) }

    if (variant == "compact") {
        HistoryList(
            variant = "compact",
            onAfterLoad = onAfterLoad,
            modifier = modifier,
        )
        return
    }

    Column(modifier = modifier) {
        LegibleText(
            text = "MY WORKOUTS",
            color = colors.text3,
            style = TextStyle(fontSize = 13.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.3.sp),
            modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 8.dp),
        )
        WorkoutList(
            variant = "lobby",
            refreshKey = workoutListKey,
            onAfterLoad = onAfterLoad,
            onWorkoutDeleted = { historyListKey++ },
        )
        LegibleText(
            text = "YOUR PROGRAMS",
            color = colors.text3,
            style = TextStyle(fontSize = 13.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.3.sp),
            modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 8.dp),
        )
        HistoryList(
            variant = "lobby",
            refreshKey = historyListKey,
            onAfterLoad = onAfterLoad,
            onWorkoutSaved = { workoutListKey++ },
        )
    }
}
