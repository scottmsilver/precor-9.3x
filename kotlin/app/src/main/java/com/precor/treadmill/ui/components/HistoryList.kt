package com.precor.treadmill.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.outlined.FavoriteBorder
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.key
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.models.HistoryEntry
import com.precor.treadmill.data.remote.models.SaveWorkoutRequest
import com.precor.treadmill.ui.theme.LegibleText
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.util.fmtDur
import android.widget.Toast
import kotlinx.coroutines.launch
import org.koin.compose.koinInject

@Composable
fun HistoryList(
    variant: String, // "lobby" or "compact"
    onAfterLoad: () -> Unit = {},
    onWorkoutSaved: () -> Unit = {},
    refreshKey: Int = 0,
    modifier: Modifier = Modifier,
    api: TreadmillApi = koinInject(),
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var history by remember { mutableStateOf<List<HistoryEntry>>(emptyList()) }

    LaunchedEffect(refreshKey) {
        runCatching { history = api.getHistory() }.onFailure { e ->
            android.util.Log.e("HistoryList", "Failed to load history", e)
            Toast.makeText(context, "Failed to load history", Toast.LENGTH_SHORT).show()
        }
    }

    val handleLoad: (String) -> Unit = { id ->
        scope.launch {
            runCatching {
                val res = api.loadFromHistory(id)
                if (res.ok) onAfterLoad()
            }.onFailure {
                Toast.makeText(context, "Failed to load program", Toast.LENGTH_SHORT).show()
            }
        }
    }

    val handleResume: (String) -> Unit = { id ->
        scope.launch {
            runCatching {
                api.resumeFromHistory(id)
                onAfterLoad()
            }.onFailure {
                Toast.makeText(context, "Failed to resume program", Toast.LENGTH_SHORT).show()
            }
        }
    }

    val handleToggleSave: (HistoryEntry) -> Unit = { entry ->
        scope.launch {
            runCatching {
                if (entry.saved && entry.savedWorkoutId != null) {
                    // Unsave by deleting the saved workout
                    api.deleteWorkout(entry.savedWorkoutId)
                } else {
                    // Save
                    val res = api.saveWorkout(SaveWorkoutRequest(historyId = entry.id))
                    if (!res.ok) {
                        Toast.makeText(context, "Failed to save workout", Toast.LENGTH_SHORT).show()
                        return@launch
                    }
                }
                history = api.getHistory()
                onWorkoutSaved()
            }.onFailure {
                Toast.makeText(context, "Failed to save workout", Toast.LENGTH_SHORT).show()
            }
        }
    }

    if (variant == "lobby") {
        Column(
            modifier = modifier.padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            history.forEach { entry ->
                key(entry.id) {
                    HistoryCard(
                        entry = entry,
                        variant = "lobby",
                        onLoad = handleLoad,
                        onResume = handleResume,
                        onToggleSave = handleToggleSave,
                    )
                }
            }
        }
    } else {
        if (history.isEmpty()) return

        Column(modifier = modifier) {
            Row(
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                LegibleText(
                    text = "RECENT PROGRAMS",
                    color = colors.text3,
                    style = TextStyle(fontSize = 13.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.3.sp),
                )
            }
            Row(
                modifier = Modifier
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                history.forEach { entry ->
                    key(entry.id) {
                        HistoryCard(
                            entry = entry,
                            variant = "compact",
                            onLoad = handleLoad,
                            onResume = handleResume,
                            onToggleSave = handleToggleSave,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun HistoryCard(
    entry: HistoryEntry,
    variant: String,
    onLoad: (String) -> Unit,
    onResume: (String) -> Unit = {},
    onToggleSave: (HistoryEntry) -> Unit = {},
) {
    val colors = LocalPrecorColors.current
    val name = entry.program?.name?.ifBlank { "Workout" } ?: "Workout"
    val intervals = entry.program?.intervals?.size ?: 0
    val duration = fmtDur(entry.totalDuration.toInt())
    val canResume = !entry.completed && entry.lastElapsed > 0
    val resumeLabel = if (canResume) "Resume from ${fmtDur(entry.lastElapsed)}" else null
    val displayName = if (entry.completed) "$name \u2713" else name

    if (variant == "lobby") {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    color = colors.card,
                    shape = MaterialTheme.shapes.medium,
                )
                .clickable { onLoad(entry.id) }
                .padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = displayName,
                    color = colors.text,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = "$duration \u00B7 $intervals interval${if (intervals != 1) "s" else ""}",
                    color = colors.text3,
                    fontSize = 12.sp,
                )
                if (entry.lastRunText.isNotBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = entry.lastRunText,
                        color = colors.text3,
                        fontSize = 11.sp,
                    )
                }
            }
            IconButton(
                onClick = { onToggleSave(entry) },
                modifier = Modifier.size(32.dp),
            ) {
                Icon(
                    imageVector = if (entry.saved) Icons.Filled.Favorite else Icons.Outlined.FavoriteBorder,
                    contentDescription = if (entry.saved) "Unsave workout" else "Save workout",
                    tint = if (entry.saved) colors.pink else colors.text3,
                    modifier = Modifier.size(20.dp),
                )
            }
            if (canResume) {
                Text(
                    text = resumeLabel ?: "",
                    color = colors.green,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier
                        .clickable { onResume(entry.id) }
                        .background(
                            color = colors.green.copy(alpha = 0.12f),
                            shape = MaterialTheme.shapes.small,
                        )
                        .padding(horizontal = 10.dp, vertical = 4.dp),
                )
            }
        }
    } else {
        Column(
            modifier = Modifier
                .width(140.dp)
                .background(
                    color = colors.card,
                    shape = MaterialTheme.shapes.medium,
                )
                .clickable { if (canResume) onResume(entry.id) else onLoad(entry.id) }
                .padding(12.dp),
        ) {
            Text(
                text = displayName,
                color = colors.text,
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                text = if (canResume) resumeLabel ?: "" else "$duration \u00B7 $intervals interval${if (intervals != 1) "s" else ""}",
                color = if (canResume) colors.green else colors.text3,
                fontSize = 11.sp,
            )
        }
    }
}
